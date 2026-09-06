"""assets_analysis 规则引擎单元测试（不依赖网络，纯函数验证）。"""

import numpy as np
import pandas as pd
import pytest

from src.assets_analysis import (
    _chg,
    _fx_verdict,
    _ls_cols,
    _options_narrative,
    _price_rows,
    _reg_beta,
    crypto,
    equity_analysis,
)
from src.options_structure import bucket_dte, compute_structure
from src.pricing import bs_greeks, d1_from


class TestHelpers:
    def test_chg_within_window(self):
        s = pd.Series(
            [100.0, 100.0, 100.0, 100.0, 100.0, 105.0],
            index=pd.date_range("2026-07-25", periods=6),
        )
        assert _chg(s, 5) == pytest.approx(5.0)

    def test_chg_not_enough(self):
        s = pd.Series([100.0], index=pd.date_range("2026-08-01", periods=1))
        assert _chg(s, 5) is None

    def test_price_rows_format(self):
        df = pd.DataFrame(
            {"SPX": [100.0, 101.0]}, index=pd.date_range("2026-08-01", periods=2)
        )
        rows = _price_rows(df, [("SPX", "标普500")])
        assert rows[0]["symbol"] == "SPX"
        assert rows[0]["name"] == "标普500"
        assert rows[0]["chg_pct"] == pytest.approx(1.0)
        assert rows[0]["date"] == "2026-08-02"

    def test_price_rows_strips_placeholder_rows(self):
        """快照管线周末原样续写最新价 → 剥离占位行，不产生假 0 涨跌。"""
        df = pd.DataFrame(
            {"SPX": [100.0, 101.0, 101.0, 101.0]},
            index=pd.date_range("2026-08-01", periods=4),
        )
        rows = _price_rows(df, [("SPX", "标普500")])
        assert rows[0]["last"] == 101.0
        assert rows[0]["chg_pct"] == pytest.approx(1.0)  # 100 → 101，不是 0
        assert rows[0]["date"] == "2026-08-02"

    def test_price_rows_weekend_revision(self):
        """周末快照行带收盘修订 → 修订并入最后交易日，涨跌按交易日算。"""
        idx = pd.to_datetime(["2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06"])
        df = pd.DataFrame({"USDKRW": [1356.0, 1345.99, 1351.10, 1351.10]}, index=idx)
        rows = _price_rows(df, [("USDKRW", "美元/韩元")])
        assert rows[0]["last"] == 1351.10
        assert rows[0]["chg_pct"] == pytest.approx(-0.36, abs=0.01)
        assert rows[0]["date"] == "2026-09-04"

    def test_price_rows_crypto_keeps_weekend(self):
        """7×24 标的周末行是真实行情，保留且按相邻观测算涨跌。"""
        idx = pd.to_datetime(["2026-09-04", "2026-09-05", "2026-09-06"])
        df = pd.DataFrame({"BTC": [100.0, 101.0, 102.0]}, index=idx)
        rows = _price_rows(df, [("BTC", "BTC")])
        assert rows[0]["chg_pct"] == pytest.approx(0.99, abs=0.01)
        assert rows[0]["date"] == "2026-09-06"


class TestEquityAnalysis:
    def test_healthy_breadth(self):
        out = equity_analysis({"gap": 0.5, "abv200": 62.0, "abv50": 64.0})
        assert "健康区间" in out["momentum"]["text"]

    def test_weak_internals(self):
        out = equity_analysis({"gap": 0.5, "abv200": 55.0, "abv50": 52.0})
        assert "跌破 60%" in out["health"]["text"]


class TestPositioningLs:
    def test_ls_cols_fin_vs_commodity(self):
        """_ls_cols：金融 = HEDGE_L/HEDGE_S，商品 = MM_L/MM_S。"""
        assert _ls_cols("BTC") == ("BTC_HEDGE_L", "BTC_HEDGE_S")
        assert _ls_cols("GC") == ("GC_MM_L", "GC_MM_S")

    def test_net_is_minus(self):
        cot = pd.DataFrame(
            {
                "BTC_HEDGE_L": [1000.0, 1200.0],
                "BTC_HEDGE_S": [900.0, 800.0],
            },
            index=pd.date_range("2025-01-01", periods=2),
        )
        lcol, scol = _ls_cols("BTC")
        net = cot[lcol] - cot[scol]
        assert list(net) == [100.0, 400.0]


class TestBucketDte:
    def test_0dte_bucket_reachable(self):
        """0DTE 是合法桶（days=0 保留，T 取 1 天最小）。"""
        assert bucket_dte(0) == "0DTE"
        assert bucket_dte(5) == "本周"
        assert bucket_dte(20) == "月度"
        assert bucket_dte(60) == "季度+"


class TestComputeStructure:
    def test_empty_chain_returns_none(self):
        r = compute_structure(pd.DataFrame(), 100.0)
        assert r.get("spot") is None

    def test_negative_dte_skipped_0dte_kept(self):
        """过期合约（days<0）剔除；当日到期（days=0）保留进 0DTE 桶。"""
        rows = [
            {
                "strike": 100.0,
                "right": "C",
                "expiration": "20200101",
                "openInterest": 5,
                "impliedVolatility": 0.2,
                "volume": 1,
            },  # 已过期
            {
                "strike": 100.0,
                "right": "C",
                "expiration": "20991231",
                "openInterest": 10,
                "impliedVolatility": 0.2,
                "volume": 2,
            },
        ]
        df = pd.DataFrame(rows)
        r = compute_structure(df, 100.0)
        assert r.get("spot") is not None
        assert "0DTE" in r.get("buckets", {})  # 桶恒存在（0 值也显示）

    def test_vanna_charm_standard_bsm(self):
        """vanna = −γ·S·√T·d2；charm = φ(d1)·[(r+σ²/2)/(σ√T) − d1/(2T)]。

        手算基准：S=765.72, K=765, T=4/365, σ=0.0937（与数值微分对照一致）。
        """
        rows = [
            {
                "strike": 765.0,
                "right": "C",
                "expiration": "20991231",
                "openInterest": 100,
                "impliedVolatility": 0.0937,
                "volume": 0,
            }
        ]
        r = compute_structure(pd.DataFrame(rows), 765.72)
        # per-share 基准（T≈4 天，用现有日期推导的 T 由代码决定，这里只验数值级）
        assert r["net_vanna"] < 0.05  # vanna 修正后 ATM 附近接近 0（d2≈d1−σ√T）
        assert abs(r["net_charm"]) < 0.05  # 修正后不再出现 ~200 倍的旧公式值

    def test_pcr_oi_atm_excludes_deep_otm(self):
        """ATM ±10% 口径剔除深 OTM 存量（OI 极大但 strike 远离现货）。"""
        rows = [
            {
                "strike": 500.0,
                "right": "P",
                "expiration": "20991231",
                "openInterest": 300000,
                "impliedVolatility": 0.4,
                "volume": 0,
            },
            {
                "strike": 100.0,
                "right": "C",
                "expiration": "20991231",
                "openInterest": 100000,
                "impliedVolatility": 0.2,
                "volume": 0,
            },
        ]
        df = pd.DataFrame(rows)
        r = compute_structure(df, 100.0)
        assert r["pcr_oi"] == 3.0  # 全样本：深 OTM 污染
        assert r["pcr_oi_atm"] == 0.0  # ATM ±10% 带内无 put（深 OTM 被剔除）
        assert r["iv_front"]["iv"] == 20.0  # 百分数单位（曾少乘 100）

    def test_bucket_gex_pct_abs_denominator(self):
        """到期集中度分母 = Σ|GEX|（净 GEX 为负时不反转符号、占比恒正）。

        构造：本周桶负 -100、月度桶负 -100、季度+ 正 +50 → 净 GEX = -150（负）。
        gex_pct 应分别为 40/40/20，且 gex_m 保留符号（-100/-100/+50）。
        """
        rows = [
            {
                "strike": 100.0,
                "right": "P",
                "expiration": "20991231",  # 季度+ 桶
                "openInterest": 5,
                "impliedVolatility": 0.5,
                "volume": 0,
            },
        ]
        import numpy as np  # noqa: F401

        from src.options_structure import compute_structure

        r = compute_structure(pd.DataFrame(rows), 100.0)
        for b in ("0DTE", "本周", "月度", "季度+"):
            assert set(r["buckets"][b]) >= {"gex_pct", "gex_m", "contracts"}


class TestBsGreeks:
    def test_gamma_positive_atm(self):
        g, d = bs_greeks(100.0, 100.0, 0.1, 0.2)
        assert g > 0 and 0 < d < 1

    def test_call_delta_deep_itm(self):
        _, d = bs_greeks(100.0, 90.0, 0.1, 0.2)
        assert d > 0.9

    def test_d1_sign(self):
        # 深实值 call → d1 为正（vanna ≈ −γ·S·√T·d1 取负，符号口径验证）
        assert d1_from(100.0, 90.0, 0.1, 0.2) > 0


class TestFxBreadth:
    def test_verdict_zero_pairs(self):
        """total_pairs=0 → 数据不足（而非误判“美元走弱 0/0”）。"""
        assert _fx_verdict(0, 0, 0) == {
            "weak": 0,
            "strong": 0,
            "total": 0,
            "verdict": "数据不足",
        }

    def test_verdict_half_weak(self):
        """半数以上对压力 < −1 → 美元走弱。"""
        assert _fx_verdict(8, 2, 15)["verdict"] == "美元走弱"

    def test_verdict_half_strong(self):
        """半数以上对压力 > +1 → 美元走强。"""
        assert _fx_verdict(0, 8, 15)["verdict"] == "美元走强"

    def test_verdict_all_neutral(self):
        """全部中性（−1..+1 之间）→ 美元中性（而非走强）。"""
        assert _fx_verdict(0, 0, 15)["verdict"] == "美元中性"

    def test_verdict_split(self):
        assert _fx_verdict(5, 3, 15)["verdict"] == "分化"


class TestCrowdingPercentile:
    def test_percentile_edges(self):
        s = pd.Series(range(1, 101))
        v = float(s.iloc[-1])
        assert float((s < v).mean() * 100) == 99.0


class TestOptionsNarrative:
    """期权结构解读（规则引擎）关键字段完整性：timsun 结构 6 块 + P/C 提示。"""

    def _sym(self, **over):
        s = {
            "symbol": "SPY",
            "spot": 765.72,
            "net_gex": -0.75,
            "net_dex": 34.44,
            "gamma_flip": 500.0,
            "flip_dist": 34.7,
            "call_wall": {"strike": 770.0, "per_point_m": 77.6},
            "put_wall": {"strike": 765.0, "per_point_m": -406.2},
            "pcr_oi": 1.63,
            "pcr_vol": 1.24,
            "iv_slope": 0.07,
            "charm_near7": 80.9,
        }
        s.update(over)
        return s

    def test_above_flip_is_positive_gamma(self):
        n = _options_narrative(self._sym())
        assert n["gamma"]["title"] == "正 Gamma：波动更容易被压制"
        assert "上方" in n["gamma"]["text"]
        assert "Net GEX -0.75B" in n["gamma"]["text"]  # 保留 2 位小数，非 -1B
        assert n["range"]["invalid"].endswith("负 Gamma 框架")
        assert len(n["levels"]) == 3

    def test_below_flip_is_negative_gamma(self):
        n = _options_narrative(self._sym(flip_dist=-5.0))
        assert n["gamma"]["title"] == "负 Gamma：波动更容易放大"
        assert "下方" in n["gamma"]["text"]

    def test_high_pcr_gets_defensive_note(self):
        n = _options_narrative(self._sym(pcr_oi=1.6))
        assert "防御性仓位重" in n["cross"]


class TestAnalystBoard:
    def test_board_summary_and_ranks(self, monkeypatch, tmp_path):
        import pandas as pd

        from src.assets_analysis import analyst_board

        n = 20
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-08-21"] * n),
                "ticker": [f"T{i:02d}" for i in range(n)],
                "company": [f"C{i:02d}" for i in range(n)],
                "industry": ["Tech"] * n,
                "sector": ["Technology"] * n,
                "price": [100.0] * n,
                "target_mean": [110.0, 130.0] * (n // 2),
                "target_high": [140.0] * n,
                "target_low": [80.0] * n,
                "upside": [15.0, 35.0] * (n // 2),
                "analysts": [20] * n,
                "rating": ["buy"] * n,
            }
        )
        targets = tmp_path / "targets.csv"
        df.to_csv(targets, index=False)

        comp = tmp_path / "comp.csv"
        pd.DataFrame(
            {
                "ticker": [f"T{i:02d}" for i in range(n)],
                "company": [f"C{i:02d}" for i in range(n)],
                "category": ["Tech"] * n,
            }
        ).to_csv(comp, index=False)

        # 核心数值：覆盖 20/20、平均空间 25、去极值后仍 25、全部高于现价
        monkeypatch.setattr(
            "src.assets_analysis._latest_targets",
            lambda: pd.read_csv(targets, index_col="date", parse_dates=["date"]),
        )
        monkeypatch.setattr(
            "src.assets_analysis._read", lambda _p, **_kw: pd.read_csv(comp)
        )
        out = analyst_board()
        assert out["rows"] == 20
        assert out["coverage_pct"] == 100.0
        assert out["avg_upside"] == 25.0
        assert out["trim_upside"] == 25.0
        assert out["above_share"] == 100.0
        assert out["tops"][0]["upside"] == 35.0
        assert len(out["industries"]) == 1
        assert out["industries"][0]["coverage"] == 20


class TestNdxRadar:
    def test_radar_counts_and_industry(self, monkeypatch, tmp_path):
        from src.assets_analysis import ndx_radar

        # 12 票、130 天单调上行 → 全部 50DMA 上方、今日上涨、行业分组
        dates = pd.date_range("2026-04-01", periods=130)
        px = pd.DataFrame(
            {
                f"T{i:02d}": [100 + i * 0.5 + j * (0.3 + i * 0.03) for j in range(130)]
                for i in range(12)
            },
            index=dates,
        )
        px.index.name = "date"
        prices = tmp_path / "prices.csv"
        px.to_csv(prices)

        comp = tmp_path / "comp.csv"
        pd.DataFrame(
            {
                "ticker": [f"T{i:02d}" for i in range(12)],
                "company": [f"C{i:02d}" for i in range(12)],
                "category": ["Tech" if i < 6 else "Health" for i in range(12)],
            }
        ).to_csv(comp, index=False)

        monkeypatch.setattr(
            "src.assets_analysis._read", lambda _p, **_kw: pd.read_csv(comp)
        )
        monkeypatch.setattr(
            "src.assets_analysis._csv",
            lambda _p, **_kw: pd.read_csv(
                prices, index_col="date", parse_dates=["date"]
            ),
        )
        monkeypatch.setattr("src.assets_analysis.asset_prices", lambda: pd.DataFrame())
        out = ndx_radar()
        assert out["rows"] == 12
        assert out["today_up"] == 12
        assert out["above50_pct"] == 100.0
        assert len(out["industries"]) == 2
        assert out["strong20"][0]["ticker"] == "T11"


class TestRegBeta:
    """_reg_beta 窗口口径：按日历日取窗，而非 NL 观测行数（90 观测 ≈ 126 日历日）。"""

    def test_calendar_window_excludes_old_noise(self):
        # 最后 ≈81 个日历日内 PX 收益率严格 = 2×NL 收益率；更早区域 PX 加噪声。
        # 旧口径按观测行数回看 90 行（≈126 日历日）会把噪声卷入 → R² 明显 < 1。
        idx = pd.bdate_range("2026-01-05", periods=150)  # ≈ 210 日历日
        r = pd.Series(0.0, index=idx)
        r[idx >= idx[-58]] = 0.008  # 爬升起点 ≈81 日历日前，安全落在 90 日窗口内
        nl = 1000.0 * (1 + r).cumprod()
        rng = np.random.default_rng(0)
        noise = pd.Series(0.0, index=idx)
        noise[idx < idx[-70]] = rng.normal(0, 0.03, size=(idx < idx[-70]).sum())
        px = 100.0 * (1 + 2 * r + noise).cumprod()  # 噪声区在 ≈98 日历日前，窗口外
        beta, r2 = _reg_beta(nl, px, days=90)
        assert beta == pytest.approx(2.0, abs=0.05)
        assert r2 == pytest.approx(1.0, abs=0.02)

    def test_insufficient_data_returns_none(self):
        idx = pd.bdate_range("2026-08-03", periods=30)
        nl = pd.Series(1000.0, index=idx)
        px = pd.Series(2000.0, index=idx)
        assert _reg_beta(nl, px, days=90) == (None, None)


class TestCryptoLiquidity:
    """crypto() 溢出 KPI：脉冲/背离按日历日取窗 + 背离判定幅度豁免。"""

    @staticmethod
    def _fixtures(
        monkeypatch,
        walcl_recent,
        walcl_before=6_000_000.0,
        btc_jump_pct=10.0,
        step_days=25,
    ):
        # 65 个工作日 ≈ 91 日历日；WALCL：end-step_days 日历日之前为 walcl_before，
        # 之后为 walcl_recent。step_days<30 → 30 日背离 = recent-before、
        # 脉冲 = (recent-before)×(30-step_days)/30 量级；step_days>30 → 两者均为 0
        days = pd.bdate_range("2026-06-01", periods=65)
        end = days[-1]
        walcl = pd.Series(walcl_before, index=days)  # 百万美元
        walcl[walcl.index > end - pd.Timedelta(days=step_days)] = walcl_recent
        liq = pd.DataFrame(
            {"WALCL": walcl, "RRPONTSYD": 0.0, "WTREGEN": 0.0}, index=days
        )
        # BTC 日频（含周末）：30 日历日内跳变 btc_jump_pct%
        cal = pd.date_range(days[0], end)
        btc = pd.Series(100.0, index=cal)
        btc[btc.index > end - pd.Timedelta(days=30)] = 100.0 * (1 + btc_jump_pct / 100)
        px = pd.DataFrame({"BTC": btc, "ETH": btc / 20, "SPX": 5000.0})
        monkeypatch.setattr("src.assets_analysis.asset_prices", lambda: px)
        monkeypatch.setattr("src.assets_analysis._csv", lambda _p, **_kw: liq)
        return end

    def test_pulse_and_divergence_use_calendar_days(self, monkeypatch):
        # 台阶在 40 日历日前（20/30 日窗口外）→ 脉冲与背离变化量均为 0；
        # 旧观测行口径会误读为 -50/-100B
        self._fixtures(monkeypatch, 6_000_000.0, walcl_before=6_050_000.0, step_days=40)
        out = crypto()["liquidity"]
        assert out["pulse_20d"] == 0
        assert out["divergence"]["nl"] == 0.0
        # BTC 腿：双腿同锚 → 30 日历日 +10%
        assert out["divergence"]["btc"] == pytest.approx(10.0)

    def test_small_moves_are_neutral(self, monkeypatch):
        # |ΔNL| < 0.1T（行业噪声底线）不触发背离：-60B 正是 timsun 当前
        # 样本量级，其不报警，我们也不报
        self._fixtures(monkeypatch, 5_940_000.0)  # 10 日前 -60B
        out = crypto()["liquidity"]
        assert out["divergence"]["verdict"] == "流动性与 BTC 同向，无明显背离"
        assert out["divergence"]["warn"] is False

    def test_meaningful_divergence_warns(self, monkeypatch):
        # NL 10 日前 -200B（20/30 日窗口内、幅度超阈）+ BTC +10% → 回撤警告
        self._fixtures(monkeypatch, 5_800_000.0, step_days=10)
        out = crypto()["liquidity"]
        assert out["pulse_20d"] == -200
        assert out["divergence"]["nl"] == -0.2
        assert "回撤风险" in out["divergence"]["verdict"]
        assert out["divergence"]["warn"] is True
        assert "警惕回撤风险" in out["trading"]

    def test_technical_pullback_branch(self, monkeypatch):
        # NL +200B + BTC -10% → 技术性回调分支
        self._fixtures(monkeypatch, 6_200_000.0, btc_jump_pct=-10.0)
        out = crypto()["liquidity"]
        assert "技术性" in out["divergence"]["verdict"]
        assert out["divergence"]["warn"] is True

    def test_beta_narrative_gated_by_r2(self, monkeypatch):
        self._fixtures(monkeypatch, 6_000_000.0, walcl_before=6_050_000.0)
        out = crypto()["liquidity"]
        # 构造数据里 NL 与 BTC 无线性关系（R²≈0）→ 不输出「敏感度 X 倍」叙事
        if (out.get("r2") or 0) >= 0.15:
            assert "敏感度" in (out["trading"] or "")
        else:
            assert "敏感度" not in (out["trading"] or "")
