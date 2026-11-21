from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from src.config import ROOT
from src.volatility_microstructure import (
    THRESHOLDS,
    compute_microstructure_state,
    generate_volatility_microstructure,
)


def test_stage_transitions_across_four_regimes():
    s1 = compute_microstructure_state(
        {
            "vix": 14.2,
            "vvix": 86.0,
            "vix9d": 13.0,
            "vix3m": 16.5,
            "skew": 136.0,
            "vixeq": 35.2,
            "cor1m": 10.5,
            "pc_equity": 0.52,
            "pc_index": 0.98,
            "agg_gex": 3.2,
        }
    )
    assert s1["stage_id"] == 1
    assert s1["score"] < 30

    s2 = compute_microstructure_state(
        {
            "vix": 19.1,
            "vvix": 112.5,
            "vix9d": 18.4,
            "vix3m": 20.0,
            "skew": 155.0,
            "vixeq": 39.0,
            "cor1m": 20.5,
            "pc_equity": 0.65,
            "pc_index": 1.12,
            "agg_gex": 0.4,
        }
    )
    assert s2["stage_id"] == 2

    s3 = compute_microstructure_state(
        {
            "vix": 35.89,
            "vvix": 138.03,
            "vix9d": 40.72,
            "vix3m": 30.94,
            "skew": 138.84,
            "vixeq": 57.91,
            "cor1m": 36.05,
            "pc_equity": 0.77,
            "pc_index": 1.27,
            "agg_gex": -5.38,
        }
    )
    assert s3["stage_id"] == 3
    assert s3["score"] >= 80
    assert all(c["met"] is True for c in s3["checklists"]["risk"])
    assert all(c["met"] is False for c in s3["checklists"]["relief"])

    s4 = compute_microstructure_state(
        {
            "vix": 19.2,
            "vvix": 96.5,
            "vix9d": 17.4,
            "vix3m": 21.4,
            "skew": 140.0,
            "vixeq": 44.0,
            "cor1m": 14.5,
            "pc_equity": 0.57,
            "pc_index": 1.01,
            "agg_gex": 2.1,
        }
    )
    assert s4["stage_id"] == 4
    assert all(c["met"] is True for c in s4["checklists"]["relief"])


def test_stage_guard_never_classifies_inverted_or_neg_gex_as_stage1_or_stage4():
    # 短端倒挂（VIX9D > VIX）绝不能落入阶段 4（全期限升水）或阶段 1（稳态）
    short_inv = compute_microstructure_state(
        {
            "vix": 19.5,
            "vvix": 95.0,
            "vix9d": 21.2,
            "vix3m": 21.5,
            "skew": 140.0,
            "vixeq": 44.0,
            "cor1m": 15.0,
            "pc_equity": 0.55,
            "pc_index": 1.00,
            "agg_gex": 1.5,
        }
    )
    assert short_inv["inverted"] is True
    assert short_inv["stage_id"] == 2

    # 负 Gamma 低波也不得落入阶段 1（稳态）
    neg_gex = compute_microstructure_state(
        {
            "vix": 15.0,
            "vvix": 88.0,
            "vix9d": 13.5,
            "vix3m": 17.0,
            "skew": 136.0,
            "vixeq": 38.0,
            "cor1m": 11.0,
            "pc_equity": 0.52,
            "pc_index": 0.98,
            "agg_gex": -1.5,
        }
    )
    assert neg_gex["stage_id"] == 2


def test_missing_nan_inf_and_kleene_or_logic():
    # NaN / inf / bool 统一清洗为 None，绝不把 NaN 算成 100 分阶段 3
    dirty = compute_microstructure_state(
        {"vix": float("nan"), "vvix": float("inf"), "agg_gex": True}
    )
    assert dirty["score"] is None
    assert dirty["stage_id"] is None
    assert all(d["score"] is None for d in dirty["dims"])
    assert all(c["state"] == "missing" for c in dirty["chain"])

    # Kleene 三值 OR：一侧为 False、另一侧为 None → None（未知）；一侧为 True、另一侧为 None → True
    half_false = compute_microstructure_state({"pc_equity": 0.55, "pc_index": None})
    r4_hf = next(c for c in half_false["checklists"]["risk"] if c["id"] == "r4")
    assert r4_hf["met"] is None
    assert "缺失" in r4_hf["read"]

    half_true = compute_microstructure_state({"pc_equity": 0.80, "pc_index": None})
    r4_ht = next(c for c in half_true["checklists"]["risk"] if c["id"] == "r4")
    assert r4_ht["met"] is True


def test_anchor_day_prevents_cross_date_ratio_mixing_and_holiday_single_point(tmp_path: Path):
    """回归 P0：慢文件窗口（T 日 VIX 缺值）与节假日单点行不得跨日混除或把期限结构退化成单点。"""
    (tmp_path / "cboe").mkdir(parents=True)
    (tmp_path / "options_structure").mkdir(parents=True)

    pd.DataFrame(
        {
            "VIX1D": [23.25, 32.06, 45.0, float("nan")],
            "VIX9D": [23.95, 29.26, 40.72, float("nan")],
            "VIX": [
                24.56,
                28.44,
                float("nan"),
                35.89,
            ],  # 第3行模拟慢文件缺VIX，第4行模拟节假日仅VIX有值
            "VIX3M": [24.29, 27.03, 30.94, float("nan")],
            "VIX6M": [24.75, 26.85, 29.54, float("nan")],
            "VIX1Y": [24.55, 25.96, 27.57, float("nan")],
            "VVIX": [118.37, 127.10, 138.03, float("nan")],
            "SKEW": [142.84, 140.86, float("nan"), float("nan")],
            "DSPX": [37.97, 37.16, 42.32, float("nan")],
            "VIXEQ": [45.16, 46.87, 57.91, float("nan")],
            "COR1M": [23.24, 30.22, 36.05, float("nan")],
            "COR3M": [20.35, 25.34, 29.71, float("nan")],
        },
        index=["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21"],
    ).rename_axis("date").to_csv(tmp_path / "cboe" / "volatility.csv")

    out = generate_volatility_microstructure(data_dir=tmp_path)
    # 必须锚定在最后一个核心列齐备的 2026-11-19，绝不混用 11-19 VIX 与 11-20 VIX3M/VIXEQ
    assert out["as_of"]["cboe"] == "2026-11-19"
    assert out["live_inputs"]["vix"] == 28.44
    assert out["live_inputs"]["vix3m"] == 27.03
    assert out["term_structure"]["date"] == "2026-11-19"
    assert out["term_structure"]["prev_date"] == "2026-11-18"
    assert all(x is not None for x in out["term_structure"]["current"])
    # metrics 与 live_inputs 推演出的比值必须完全一致
    expected_vr = round(28.44 / 27.03, 3)
    assert out["metrics"]["vix_vix3m"]["value"] == expected_vr
    assert out["term_structure"]["is_backwardation"] is True


def test_generate_microstructure_from_isolated_tmp_dir(tmp_path: Path):
    (tmp_path / "cboe").mkdir(parents=True)
    (tmp_path / "options_structure").mkdir(parents=True)

    pd.DataFrame(
        {
            "VIX1D": [18.0, 32.0],
            "VIX9D": [19.0, 30.0],
            "VIX": [20.0, 28.5],
            "VIX3M": [21.5, 27.0],
            "VIX6M": [22.0, 26.5],
            "VIX1Y": [22.5, 26.0],
            "VVIX": [98.0, 127.0],
            "SKEW": [142.0, 141.0],
            "DSPX": [36.0, 39.0],
            "VIXEQ": [46.0, 47.0],
            "COR1M": [15.0, 30.0],
            "COR3M": [16.0, 25.0],
        },
        index=["2026-11-18", "2026-11-19"],
    ).rename_axis("date").to_csv(tmp_path / "cboe" / "volatility.csv")

    pd.DataFrame(
        {
            "TOTAL_PC": [0.95, 1.05],
            "INDEX_PC": [1.05, 1.22],
            "ETP_PC": [1.08, 1.15],
            "EQUITY_PC": [0.60, 0.74],
            "VIX_PC": [0.85, 1.10],
            "SPX_PC": [1.18, 1.32],
        },
        index=["2026-11-18", "2026-11-19"],
    ).rename_axis("date").to_csv(tmp_path / "cboe" / "put_call.csv")

    # 额外放一份含非法 NaN 的坏快照，验证不会拖垮整个接口
    (tmp_path / "options_structure" / "20261118.json").write_text(
        '{"snapshot_date":"2026-11-18","symbols":{"SPY":{"net_gex":NaN},"QQQ":null,"IWM":{"net_gex":-1}}}',
        encoding="utf-8",
    )
    snap = {
        "date": "2026-11-20T02:17:24Z",
        "snapshot_date": "2026-11-19",
        "oi_final": True,
        "symbols": {
            "SPY": {
                "spot": 715.8,
                "net_gex": -2.28,
                "net_dex": 170.0,
                "put_wall": {"strike": 700},
                "call_wall": {"strike": 775},
            },
            "QQQ": {
                "spot": 645.0,
                "net_gex": -0.71,
                "net_dex": 150.0,
                "put_wall": {"strike": 650},
                "call_wall": {"strike": 700},
            },
            "IWM": {
                "spot": 264.2,
                "net_gex": -2.39,
                "net_dex": 50.0,
                "put_wall": {"strike": 265},
                "call_wall": {"strike": 270},
            },
        },
    }
    (tmp_path / "options_structure" / "20261119.json").write_text(
        json.dumps(snap), encoding="utf-8"
    )

    out = generate_volatility_microstructure(data_dir=tmp_path)
    assert "error" not in out
    assert out["as_of"]["options"] == "2026-11-19"
    assert out["dealer"]["agg_gex"] == -5.38
    assert out["stage_id"] == 3
    assert len(out["history"]["vixeq_vix"]) == 2
    dumped = json.dumps(out, ensure_ascii=False)
    assert '"color"' not in dumped


def test_thresholds_fall_within_real_1y_distribution():
    vol_path = ROOT / "data" / "cboe" / "volatility.csv"
    pc_path = ROOT / "data" / "cboe" / "put_call.csv"
    if not vol_path.exists() or not pc_path.exists():
        pytest.skip("缺少本地 CBOE 历史数据")

    vol = pd.read_csv(vol_path, index_col="date", parse_dates=True).tail(252)
    pc = pd.read_csv(pc_path, index_col="date", parse_dates=True).tail(252)
    if "VIXEQ" not in vol.columns or "COR1M" not in vol.columns:
        pytest.skip("本地 volatility.csv 尚未拉取 VIXEQ/COR1M")

    checks = [
        ("vix_risk", vol["VIX"].dropna(), THRESHOLDS["vix_risk"]),
        ("vvix_warn", vol["VVIX"].dropna(), THRESHOLDS["vvix_warn"]),
        ("skew_warn", vol["SKEW"].dropna(), THRESHOLDS["skew_warn"]),
        (
            "disp_risk",
            (vol["VIXEQ"] / vol["VIX"].where(vol["VIX"] > 0)).dropna(),
            THRESHOLDS["disp_risk"],
        ),
        ("cor1m_risk", vol["COR1M"].dropna(), THRESHOLDS["cor1m_risk"]),
        ("pc_equity_risk", pc["EQUITY_PC"].dropna(), THRESHOLDS["pc_equity_risk"]),
        ("pc_index_risk", pc["INDEX_PC"].dropna(), THRESHOLDS["pc_index_risk"]),
        ("pc_total_risk", pc["TOTAL_PC"].dropna(), THRESHOLDS["pc_total_risk"]),
        ("pc_vix_risk", pc["VIX_PC"].dropna(), THRESHOLDS["pc_vix_risk"]),
        ("pc_spx_risk", pc["SPX_PC"].dropna(), THRESHOLDS["pc_spx_risk"]),
    ]
    for name, s, thr in checks:
        assert len(s) >= 100, f"{name} 历史样本不足"
        pct = float((s <= thr).mean() * 100)
        assert 3.0 <= pct <= 97.0, f"{name}={thr} 落在历史极端外侧（分位 {pct:.1f}%）"


def test_api_route_registered_and_isolated(monkeypatch, tmp_path: Path):
    from fastapi import HTTPException

    from src import server

    paths = {getattr(r, "path", None) for r in server.app.routes}
    assert "/api/volatility/microstructure" in paths

    # 空目录时路由必须抛 HTTPException 404（供 export_pages 跳过而不是写出 error JSON）
    monkeypatch.setattr(
        server,
        "generate_volatility_microstructure",
        lambda: generate_volatility_microstructure(data_dir=tmp_path),
    )
    with pytest.raises(HTTPException) as exc_info:
        server.get_volatility_microstructure()
    assert exc_info.value.status_code == 404
    assert math.isfinite(THRESHOLDS["vix_risk"])
