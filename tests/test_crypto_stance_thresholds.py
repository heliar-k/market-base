import json
from pathlib import Path

from src.assets_analysis import LS_HIGH, LS_LOW, PCR_HIGH, PCR_LOW

ROOT = Path(__file__).resolve().parents[1]
WINDOW = 20

# 散户通道阈值（src/assets_analysis.py 的 PCR_LOW/HIGH、LS_LOW/HIGH）按 78 天实测
# 分布取 p10/p90。市场结构漂移时阈值会落到分布之外，页面症状是「散户立场长期不变」。
# 这里锁两件事：阈值在历史分布里的分位位置，以及最近一段时间读数没有全落同一侧。
# 变红时人工复核常量，不做自动重算——立场标签要跨时间可比。


def _pcr_series() -> list[float]:
    """Deribit BTC 期权 put/call OI 比快照序列（按日期升序）。"""
    out: list[float] = []
    for f in sorted((ROOT / "data" / "crypto_derivatives").glob("20*.json")):
        pcr = (json.loads(f.read_text(encoding="utf-8")).get("options_BTC") or {}).get("pcr")
        if pcr:
            out.append(float(pcr))
    return out


def _ls_series() -> list[float]:
    """Coinglass 24h 多空账户比快照序列（long_pct / short_pct，按日期升序）。"""
    out: list[float] = []
    for f in sorted((ROOT / "data" / "coinglass").glob("20*.json")):
        r = json.loads(f.read_text(encoding="utf-8")).get("ls_ratio") or {}
        if r.get("long_pct") and r.get("short_pct"):
            out.append(r["long_pct"] / r["short_pct"])
    return out


def _cases() -> list[tuple[str, list[float], float, float]]:
    """(通道名, 历史读数, 下界, 上界)。"""
    return [
        ("期权 PCR", _pcr_series(), PCR_LOW, PCR_HIGH),
        ("多空账户比", _ls_series(), LS_LOW, LS_HIGH),
    ]


def _pct_rank(values: list[float], value: float) -> float:
    return sum(1 for v in values if v <= value) / len(values) * 100


def test_thresholds_sit_inside_distribution():
    for label, values, lo, hi in _cases():
        assert len(values) >= 40, f"{label} 历史只有 {len(values)} 个观测，无法校验分位"
        lo_pct = _pct_rank(values, lo)
        hi_pct = _pct_rank(values, hi)
        assert 3 <= lo_pct <= 40, (
            f"{label} 下界 {lo} 落在历史 {lo_pct:.0f} 分位（应落在 3–40），"
            "太低等于永不触发、太高会频繁误触"
        )
        assert 60 <= hi_pct <= 97, (
            f"{label} 上界 {hi} 落在历史 {hi_pct:.0f} 分位（应落在 60–97），"
            "太低会频繁误触、太高等于永不触发"
        )


def test_recent_readings_not_all_on_one_side():
    for label, values, lo, hi in _cases():
        sides = {"低" if v <= lo else ("高" if v >= hi else "中") for v in values[-WINDOW:]}
        assert len(sides) > 1, (
            f"{label} 最近 {WINDOW} 个观测全落在「{next(iter(sides))}」侧，"
            "立场恒定、对照卡没有区分度；复核 src/assets_analysis.py 的阈值常量"
        )
