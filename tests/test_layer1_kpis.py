"""LAYER 1 · NOW 8-KPI 卡分析层单元测试。

tmp_path 隔离（monkeypatch assets_analysis.ROOT）+ monkeypatch 网络函数，
不真实联网。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

import src.assets_analysis as aa

_LABELS = [
    "BTC 现价",
    "BTC 30d 已实现波动率",
    "基差 60d EMA",
    "Spread（基差−SOFR）",
    "永续资金费率 8h",
    "CME BTC OI",
    "永续多空比",
    "期权 Put/Call OI",
]


def _write(root, path: str, text: str) -> None:
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _snapshot(last: float = 100.0, pcr: float = 0.8, fut_oi: int = 1000) -> dict:
    return {
        "ts": "2026-08-23T00:00:00Z",
        "title": "加密衍生品快照",
        "perp": {
            "BTC": {
                "funding_rate": 0.0001,
                "funding_annual": 0.1095,
                "oi_usd": 1e9,
                "oi_ccy": 1000.0,
                "last": last,
                "ts": 1,
            }
        },
        "taker": {
            "BTC": [
                {"date": "2026-08-22", "buy": 100.0, "sell": 80.0},
                {"date": "2026-08-21", "buy": 90.0, "sell": 100.0},
                {"date": "2026-08-20", "buy": 110.0, "sell": 90.0},
            ]
        },
        "options_BTC": {
            "spot_anchor": last,
            "pcr": pcr,
            "call_wall": 110.0,
            "put_wall": 90.0,
        },
        "cme": {"fut_price": 101.0, "spot": 100.0, "basis_pct": 1.0, "fut_oi": fut_oi},
    }


def _mk(
    tmp_path,
    prices: list[float] | None = None,
    snap_last: float | None = None,
    pcr: float = 0.8,
) -> None:
    """最小 fixture：BTC 日线 + 基差 + SOFR + COT + 2 个快照 json。"""
    root = tmp_path
    prices = prices or [float(i) for i in range(1, 101)]
    n = len(prices)
    d = pd.bdate_range("2024-01-01", periods=n)
    _write(
        root,
        "data/yfinance/asset_prices.csv",
        "date,BTC\n" + "".join(f"{dd.date()},{p}\n" for dd, p in zip(d, prices)),
    )
    _write(
        root,
        "data/crypto_basis/basis.csv",
        "date,basis_pct,fut_close,spot_close\n"
        + "".join(f"{dd.date()},{p / 10},100,95\n" for dd, p in zip(d, prices)),
    )
    _write(
        root,
        "data/fred/rates/rates.csv",
        "date,SOFR\n2026-08-01,3.5\n2026-08-02,3.6\n",
    )
    w = pd.date_range("2025-01-06", periods=n, freq="W-FRI")
    _write(
        root,
        "data/cot/cot.csv",
        "date,BTC_OI\n"
        + "".join(
            f"{dd.date()},{int(1000 + 1000 * p / 100)}\n" for dd, p in zip(w, prices)
        ),
    )
    last = snap_last if snap_last is not None else prices[-1]
    for i in range(2):
        _write(
            root,
            f"data/crypto_derivatives/2026080{i + 1}.json",
            json.dumps(_snapshot(last=last, pcr=pcr)),
        )


def _mock_net(monkeypatch) -> None:
    """mock 网络拉取函数（funding ≥30 条、taker 10 条）。

    taker 用降序（OKX 实测最新在前，与生产一致——KPI7 chg 依赖 iloc[0]/iloc[1]）。
    """
    monkeypatch.setattr(
        aa, "_okx_funding_history", lambda: [0.0001 * i for i in range(1, 41)]
    )
    monkeypatch.setattr(
        aa, "_okx_taker_history", lambda: [1.09 - 0.01 * i for i in range(10)]
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """完整数据环境（ROOT→tmp_path + 网络 mock）。"""
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path)
    _mock_net(monkeypatch)
    return tmp_path


def _by_label(d: dict) -> dict:
    return {k["label"]: k for k in d["kpis"]}


def test_layer1_eight_kpis_and_fields(setup):
    d = aa._layer1_kpis()
    kpis = d["kpis"]
    assert [k["label"] for k in kpis] == _LABELS
    for k in kpis:
        fields = {"label", "value", "pct_rank", "pct_label", "chg", "quartiles", "note"}
        assert fields <= set(k)


def test_pct_rank_known_series(setup):
    """BTC 价 1..100、快照 last=100 → 百分位 100.0；P50=50.5；chg=↑+1.01%。"""
    by = _by_label(aa._layer1_kpis())
    k = by["BTC 现价"]
    assert k["pct_rank"] == 100.0
    assert k["value"] == "$100"
    assert k["chg"] == "↑ +1.01%"
    q = k["quartiles"]
    assert q is not None and len(q) == 3
    assert abs(q[1] - 50.5) < 0.01
    assert k["pct_label"] == "过去 100 天 · 第 100 百分位"


def test_pct_rank_mid_series(tmp_path, monkeypatch):
    """BTC 价 1..100、快照 last=50 → 百分位 50.0。"""
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path, snap_last=50.0)
    _mock_net(monkeypatch)
    k = _by_label(aa._layer1_kpis())["BTC 现价"]
    assert k["pct_rank"] == 50.0
    assert k["value"] == "$50"


def test_cme_oi_kpi_uses_cot_caliber(tmp_path, monkeypatch):
    """KPI6 口径：当前值必须用 COT 周频全合约 OI（快照 fut_oi 是近月单合约不可比）。

    快照 fut_oi=1（远小于 COT 基线）→ 若误用快照值，value 会是 1 份；
    修复应取 COT 末值（fixture: 1000 + 1000×p/100 → 2000）。
    """
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path)
    _write(
        tmp_path,
        "data/crypto_derivatives/20260801.json",
        json.dumps(_snapshot(last=100.0, fut_oi=1)),
    )
    _mock_net(monkeypatch)
    k = _by_label(aa._layer1_kpis())["CME BTC OI"]
    assert k["value"] == "2,000 份"
    assert "近 1 年" in k["pct_label"]
    assert "第 " in k["pct_label"]
    assert "COT 全合约口径" in k["note"]
    assert k["pct_rank"] is not None


def test_cme_oi_small_sample_keeps_note(tmp_path, monkeypatch):
    """COT 样本 <30 周：note 必须保留"样本不足"解释，不能被口径说明覆盖。"""
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path, prices=[float(i) for i in range(1, 11)])  # COT 仅 10 周
    _mock_net(monkeypatch)
    k = _by_label(aa._layer1_kpis())["CME BTC OI"]
    assert k["pct_rank"] is None
    assert "样本" in k["note"] and "COT 全合约口径" in k["note"]


def test_insufficient_data_pct_none(tmp_path, monkeypatch):
    """10 天数据 + 5 条 funding + 3 条 taker + 2 个快照。

    全部 pct_rank=None 且带 note。
    """
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path, prices=[float(i) for i in range(1, 11)])
    monkeypatch.setattr(aa, "_okx_funding_history", lambda: [0.0001] * 5)
    monkeypatch.setattr(aa, "_okx_taker_history", lambda: [1.0] * 3)
    d = aa._layer1_kpis()
    for k in d["kpis"]:
        assert k["pct_rank"] is None, k["label"]
        assert k["pct_label"] == "历史不足", k["label"]
        assert k["note"], k["label"]
    by = _by_label(d)
    assert "快照积累中" in by["期权 Put/Call OI"]["note"]
    assert "样本" in by["BTC 现价"]["note"]


def test_network_failure_degrades(tmp_path, monkeypatch):
    """OKX 网络异常（fetcher._okx 抛错）→ 拉取函数返回 []、KPI 不抛异常。"""
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr("src.fetchers.crypto_derivatives_fetcher._okx", boom)
    assert aa._okx_funding_history() == []
    assert aa._okx_taker_history() == []

    d = aa._layer1_kpis()  # funding/taker 历史为空 → 降级，仍返回 8 个 KPI
    assert len(d["kpis"]) == 8
    by = _by_label(d)
    assert by["永续资金费率 8h"]["pct_rank"] is None
    # 快照仍有当前值（funding/taker 来自 json，不依赖网络）
    assert by["永续资金费率 8h"]["value"] == "0.0100%"
    assert by["永续多空比"]["value"] == "1.11"
    assert by["永续多空比"]["pct_rank"] is None
    # 网络失败 → 快照 taker 兑底（降序 [100/80, 90/100, 110/90]）：chg = 1.25/0.9−1
    assert by["永续多空比"]["chg"] == "↑ +38.89%"


def test_funding_kpi_uses_mocked_history(setup, monkeypatch):
    """funding 历史被 mock → 百分位用当前值在历史里的位置。"""
    monkeypatch.setattr(
        aa, "_okx_funding_history", lambda: [0.006 * i for i in range(1, 41)]
    )
    by = _by_label(aa._layer1_kpis())
    k = by["永续资金费率 8h"]
    # 当前 0.01%（0.0001×100），历史 0.006%..0.24% 共 40 条 → 仅 1 条 ≤ 0.01 → rank 2.5
    assert k["pct_rank"] == 2.5
    assert k["quartiles"] is not None
    # 降级路径（现场拉的历史）与快照 cur 时间轴无法对齐 → chg 置 None
    assert k["chg"] is None


def test_funding_kpi_degraded_chg_none(setup):
    """快照无 funding_hist（默认降级现拉，40 条足够）→ chg 仍为 None。

    旧行为会算出一个错位的 1d 变化；修复后仅快照自带 hist 才出 chg。
    """
    k = _by_label(aa._layer1_kpis())["永续资金费率 8h"]
    assert k["chg"] is None


def test_crypto_derivatives_wires_layer1(setup):
    d = aa.crypto_derivatives()
    assert d is not None
    assert "layer1" in d
    assert len(d["layer1"]["kpis"]) == 8


def test_taker_pct_rank_with_10_rows(setup):
    """KPI7 min_n=7：10 条 taker 历史下 pct_rank 非 None（原 min_n=30 永不可达）。"""
    k = _by_label(aa._layer1_kpis())["永续多空比"]
    # 当前 1.111 ≥ 历史最大 1.09 → 第 100 百分位
    assert k["pct_rank"] == 100.0
    assert k["pct_label"] == "过去 10 日 · 第 100 百分位"
    # 降序历史（最新在前）：chg = iloc[0]/iloc[1] = 1.09/1.08 → 上升
    assert k["chg"] == "↑ +0.93%"


def test_funding_kpi_snapshot_hist_path(tmp_path, monkeypatch):
    """快照自带 funding_hist（升序 %）→ KPI5 走快照路径：24h 前 = 倒数第 4 条。

    若不慎走了降级路径（_okx_funding_history），chg 会变成历史第 N 个值之差。
    """
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path)
    snap = _snapshot()
    # 升序 8h 结算费率（%）：cur=0.01 为下一结算点，hist[-1]=上一结算点
    # （结构化差 1 格）→ 24h 参照 = hist[-3] = 0.008
    snap["funding_hist"] = [0.0052, 0.008, 0.009, 0.01]
    _write(tmp_path, "data/crypto_derivatives/20260802.json", json.dumps(snap))
    # 网络历史 mock 成与快照不同的序列：若误走降级路径会取不到 0.008
    monkeypatch.setattr(
        aa, "_okx_funding_history", lambda: [0.0099, 0.0098, 0.0097, 0.0096, 0.0095]
    )
    k = _by_label(aa._layer1_kpis())["永续资金费率 8h"]
    # 当前 0.01% − 24h 前 0.008% = +0.0020pp（百分点差用 pp）；若误用 [-4] 会取到 0.0052
    assert k["chg"] == "↑ +0.0020pp"


def test_funding_kpi_snapshot_hist_len3_chg_none(tmp_path, monkeypatch):
    """快照 funding_hist 恰 3 条（<4）→ chg 置 None。

    iloc[-3] 索引合法，但 guard len>=4 拦截：3 条不足以建立 24h 对齐窗口
    （锁定当前护栏行为，防止放宽 guard 后无测试守护）。
    """
    monkeypatch.setattr(aa, "ROOT", tmp_path)
    _mk(tmp_path)
    snap = _snapshot()
    snap["funding_hist"] = [0.0052, 0.008, 0.009]
    _write(tmp_path, "data/crypto_derivatives/20260802.json", json.dumps(snap))
    k = _by_label(aa._layer1_kpis())["永续资金费率 8h"]
    assert k["chg"] is None
