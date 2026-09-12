"""BTC 前瞻雷达口径回归测试：coinglass 空快照回退 + 7d 变化 + 中性带。"""

import json

import pytest

import src.assets_analysis as aa


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """构造 data/coinglass + data/crypto_derivatives 最小目录，monkeypatch ROOT。"""
    (tmp_path / "data" / "cot").mkdir(parents=True)
    cg = tmp_path / "data" / "coinglass"
    cg.mkdir(parents=True)
    # 09-04：CME OI 100000
    (cg / "20260904.json").write_text(
        json.dumps(
            {
                "ts": "x",
                "all_open_interest_usd": 1,
                "ls_ratio": {"long_pct": 48.0, "short_pct": 52.0},
                "exchanges": [{"name": "CME", "oi_btc": 100000.0}],
            }
        )
    )
    # 09-10：CME OI 105000（7d +5%）
    (cg / "20260910.json").write_text(
        json.dumps(
            {
                "ts": "x",
                "all_open_interest_usd": 1,
                "ls_ratio": {"long_pct": 48.36, "short_pct": 51.64},
                "exchanges": [{"name": "CME", "oi_btc": 105000.0}],
            }
        )
    )
    # 09-11：抓取失败的空快照（只有 ts/title）→ 必须被跳过
    (cg / "20260911.json").write_text(json.dumps({"ts": "x", "title": "空"}))
    cd = tmp_path / "data" / "crypto_derivatives"
    cd.mkdir(parents=True)
    for day, oi in (("20260904", 2.2e9), ("20260911", 2.1e9)):
        (cd / f"{day}.json").write_text(json.dumps({"perp": {"BTC": {"oi_usd": oi}}}))
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    return tmp_path


def test_coinglass_skips_empty_snapshot(data_root):
    cg = aa._coinglass()
    assert cg["_date"] == "20260910"  # 回退到最近非空快照
    assert cg["ls_ratio"]["long_pct"] == 48.36


def test_cme_oi_7d_change(data_root):
    chg = aa._chg_nd(aa._coinglass_series(), aa._cme_oi)
    assert chg == pytest.approx(5.0)


def test_perp_oi_7d_change(data_root):
    chg = aa._chg_nd(aa._perp_oi_series(), lambda j: j["oi"])
    assert chg == pytest.approx(-100 / 22)  # 2.1/2.2 - 1 ≈ -4.5%，±5% 中性带内


def test_radar_neutral_bands(data_root):
    snap = {
        "perp": {
            "BTC": {"funding_rate": 4e-5, "funding_annual": 0.05, "oi_usd": 2.1e9}
        },
        "options_BTC": {
            "spot_anchor": 77390.0,
            "pcr": 0.55,
            "near_max_pain": 78000.0,
            "near_call_wall": 81000.0,
            "near_put_wall": 75000.0,
        },
        "cme": {},
        "basis": {"available": False},
        "etf": {"available": False},
        "coinglass": aa._coinglass(),
    }
    r = aa.crypto_radar(snap)
    sig = {s["name"]: s for s in r["signals"]}
    assert sig["CME 机构头寸"]["dir"] == 1  # 无 COT → 回退 Coinglass +5%（±1% 带外）
    assert sig["散户多空比"]["dir"] == 0  # L/S 0.94 在中性带
    assert sig["永续 OI"]["dir"] == 0  # -4.5% 在 ±5% 中性带
    assert sig["期权牵引"]["dir"] == 1  # 无 gex → 回退 Max Pain 距现价 +0.8%
    assert sig["资金费率"]["dir"] == 0  # 年化 5% 未超 ±15% 拥挤阈值
    assert sig["资金费率"]["weight"] == 5  # 降权为拥挤度过滤器
    # 计分权重：CME15+资金5+期权10+永续10+多空比10 = 50
    assert r["confidence"] == round(50 / 95 * 100)


def test_radar_cme_missing_driver(data_root, monkeypatch):
    """CME 数据全缺（无 coinglass 无 COT）→ driver 走现货分支，不抛 TypeError。"""
    monkeypatch.setattr(aa, "_coinglass_series", lambda: [])
    snap = {
        "perp": {},
        "options_BTC": {},
        "cme": {},
        "basis": {"available": False},
        "etf": {"available": False},
        "coinglass": {},
    }
    r = aa.crypto_radar(snap)
    assert r["driver"].startswith("现货驱动")
    # 仅永续 OI（读快照历史）可计分（dir 0 中性带也计权重）→ 10/95
    assert r["confidence"] == round(10 / 95 * 100)


def test_radar_cftc_net_preferred_and_gex(data_root, tmp_path):
    """COT 存在时 CME 信号用大投机净头寸 4 周变化；有 gex 时期权用 Gamma Flip。"""
    import pandas as pd

    dates = pd.date_range("2026-08-04", periods=6, freq="7D")
    pd.DataFrame(
        {
            "date": dates,
            "BTC_ASSET_L": [2000] * 5 + [4000],
            "BTC_ASSET_S": [0] * 6,
        }
    ).to_csv(tmp_path / "data" / "cot" / "cot.csv", index=False)
    snap = {
        "perp": {"BTC": {"funding_rate": 4e-5, "funding_annual": 0.05}},
        "options_BTC": {
            "spot_anchor": 77390.0,
            "gex": {"net_gex_musd": 120.0, "gamma_flip": 76000.0},
        },
        "cme": {},
        "basis": {"available": False},
        "etf": {"available": False},
        "coinglass": aa._coinglass(),
    }
    sig = {s["name"]: s for s in aa.crypto_radar(snap)["signals"]}
    assert sig["CME 机构头寸"]["dir"] == 1  # 净头寸 2000→4000，+100% 超 ±10% 带
    assert "CFTC" in sig["CME 机构头寸"]["desc"]
    assert sig["期权牵引"]["dir"] == 1  # 现价在 flip 上方 +1.8%
    assert "Gamma Flip" in sig["期权牵引"]["desc"]
