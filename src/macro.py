"""
宏观派生指标计算模块 — 从原始 FRED 系列派生时序指标。

本模块拥有 FRED 分类的**加载管线**：读分类 CSV → 合并跨分类伙伴 →
derive_macro 追加派生列。
派生公式 / 单位换算（如 RRPONTSYD×1000）/ 伙伴归属都以本模块为权威定义，
调用方（TUI / server / CLI）只调 read/load_* 三个入口，不得自行读 CSV 或预乘原始列。

派生指标清单（公式 / 单位 / 数据源列名）：
  - SPREAD_2S10S        = DGS10 − DGS2                 （百分点，国债收益率曲线斜率）
  - SPREAD_3M10S        = DGS10 − DGS3MO               （百分点，3 月期-10 年期利差）
  - SPREAD_5S30S        = DGS30 − DGS5                 （百分点，5 年期-30 年期利差）
  - NET_LIQUIDITY       = WALCL − RRPONTSYD×1000 − WTREGEN（百万美元，联储净流动性）
  - BEI_5Y              = (DGS5 − DFII5) × 100         （bp，5 年期盈亏平衡通胀率）
  - BEI_7Y              = (DGS7 − DFII7) × 100         （bp，7 年期盈亏平衡通胀率）
  - BEI_10Y             = (DGS10 − DFII10) × 100       （bp，10 年期盈亏平衡通胀率）
  - BEI_20Y             = (DGS20 − DFII20) × 100       （bp，20 年期盈亏平衡通胀率）
  - BEI_30Y             = (DGS30 − DFII30) × 100       （bp，30 年期盈亏平衡通胀率）
  - SOFR_IORB_SPREAD_BP = (SOFR − IORB) × 100          （bp，融资-准备金利率利差）

设计原则：
  - 派生列只在所有输入列都存在时才计算，缺列则跳过（不报错）。
  - 派生列覆盖同名已存在列（本模块是权威定义）。
"""

from collections.abc import Callable
from pathlib import Path

import pandas as pd


def _spread(a: str, b: str, scale: float = 1.0) -> Callable[[pd.DataFrame], pd.Series]:
    """派生函数工厂：(df[a] − df[b]) × scale。"""
    return lambda df: (df[a] - df[b]) * scale


def _net_liquidity(df: pd.DataFrame) -> pd.Series:
    """净流动性 = WALCL − RRPONTSYD×1000 − WTREGEN（百万美元，联储净流动性）。

    RRPONTSYD（隔夜逆回购）在 FRED 单位为**十亿美元**，WALCL（联储总资产）、
    WTREGEN（财政部一般账户 TGA）为百万美元——这里把 RRP×1000 统一到百万美元。
    """
    return df["WALCL"] - df["RRPONTSYD"] * 1000 - df["WTREGEN"]


# 派生指标 → (输入列, 计算函数) 单一规格：derive_macro 计算与 UI 输入列反查共用。
# 派生列只在所有输入列都存在时才计算（缺列跳过，不报错），覆盖同名已存在列。
# 注意：BEI_* 需要名义(DGS*)+实际(DFII*)两列，单分类 CSV 不会同时有，
# 故按分类加载时不会生成——这里仍列出输入要求，UI 按实际 df 列决定是否画。
DERIVED: dict[str, tuple[tuple[str, ...], Callable[[pd.DataFrame], pd.Series]]] = {
    "SPREAD_2S10S": (("DGS10", "DGS2"), _spread("DGS10", "DGS2")),
    "SPREAD_3M10S": (("DGS10", "DGS3MO"), _spread("DGS10", "DGS3MO")),
    "SPREAD_5S30S": (("DGS30", "DGS5"), _spread("DGS30", "DGS5")),
    "NET_LIQUIDITY": (("WALCL", "RRPONTSYD", "WTREGEN"), _net_liquidity),
    "BEI_5Y": (("DGS5", "DFII5"), _spread("DGS5", "DFII5", 100)),
    "BEI_7Y": (("DGS7", "DFII7"), _spread("DGS7", "DFII7", 100)),
    "BEI_10Y": (("DGS10", "DFII10"), _spread("DGS10", "DFII10", 100)),
    "BEI_20Y": (("DGS20", "DFII20"), _spread("DGS20", "DFII20", 100)),
    "BEI_30Y": (("DGS30", "DFII30"), _spread("DGS30", "DFII30", 100)),
    "SOFR_IORB_SPREAD_BP": (("SOFR", "IORB"), _spread("SOFR", "IORB", 100)),
}

# 派生指标 → 所需输入列（server / tests 的反查视图，由 DERIVED 派生，勿另写）。
DERIVED_INPUTS = {name: inputs for name, (inputs, _) in DERIVED.items()}


def derived_series_for_category(category: str) -> list[str]:
    """返回某 FRED 分类加载后可能生成的派生系列名（输入列都在该分类内时）。

    按 FRED_SERIES 的分类归属判断输入列是否同分类可得。
    跨分类派生（如 BEI 需 rates+tips）不在此返回——见 cross_category_series_for。
    """
    from src.config import FRED_SERIES

    cat_metrics = set(FRED_SERIES.get(category, {}).keys())
    out: list[str] = []
    for derived, inputs in DERIVED_INPUTS.items():
        if set(inputs).issubset(cat_metrics):
            out.append(derived)
    return out


def cross_category_series_for(category: str) -> list[str]:
    """返回某分类可参与但输入列跨多分类的派生系列名（BEI 横跨 rates+tips）。

    判断条件：该分类贡献 ≥1 输入列，但非全部输入列都在该分类内
    （否则属单分类派生，已由 derived_series_for_category 返回）。
    """
    from src.config import FRED_SERIES

    cat_metrics = set(FRED_SERIES.get(category, {}).keys())
    out: list[str] = []
    for derived, inputs in DERIVED_INPUTS.items():
        inputs_set = set(inputs)
        contributes = bool(inputs_set & cat_metrics)
        all_in_cat = inputs_set.issubset(cat_metrics)
        if contributes and not all_in_cat:
            out.append(derived)
    return out


def cross_category_partners(category: str) -> list[str]:
    """返回需与 category 合并 CSV 才能算出跨分类派生的其它分类名。

    扫描 cross_category_series_for(category) 里每个派生系的输入列，
    收集不在 category 内的输入列所属的其它分类。去重保序。
    """
    from src.config import FRED_SERIES

    # metric → category 反查表
    metric_to_cat: dict[str, str] = {}
    for cat, series_map in FRED_SERIES.items():
        for metric in series_map:
            metric_to_cat[metric] = cat

    cat_metrics = set(FRED_SERIES.get(category, {}).keys())
    partners: list[str] = []
    seen: set[str] = set()
    for derived in cross_category_series_for(category):
        for metric in DERIVED_INPUTS[derived]:
            if metric in cat_metrics:
                continue  # 本分类自己的输入列
            partner = metric_to_cat.get(metric)
            if partner is not None and partner not in seen and partner != category:
                seen.add(partner)
                partners.append(partner)
    return partners


def derive_macro(df: pd.DataFrame) -> pd.DataFrame:
    """对含原始 FRED 列的 df 追加派生宏观指标列。

    输入列缺失时跳过对应派生，不报错。派生列覆盖同名已存在列（本模块是权威定义）。
    """
    out = df.copy()
    for col, (inputs, fn) in DERIVED.items():
        if set(inputs).issubset(out.columns):
            out[col] = fn(out)
    return out


# ── FRED 分类加载管线 ────────────────────────────────────────────────────────


def _fred_csv_path(category: str) -> Path:
    """FRED 分类 CSV 路径（以 config.ROOT 为准）。"""
    from src.config import ROOT

    return ROOT / "data" / "fred" / category / f"{category}.csv"


def read_macro_category(category: str) -> pd.DataFrame:
    """读取某 FRED 分类的原始 CSV（date 索引，原始单位，不派生）。

    CSV 缺失抛 FileNotFoundError，由调用方决定 404 / 占位提示。
    """
    path = _fred_csv_path(category)
    if not path.exists():
        raise FileNotFoundError(f"FRED 分类 {category} 无数据: {path}")
    return pd.read_csv(path, index_col="date", parse_dates=True)


def load_macro_category(category: str) -> pd.DataFrame:
    """加载单分类：原始列 + 跨分类伙伴(left join) + 派生列。

    单位归一化（如 RRPONTSYD×1000）只在 derive_macro 的公式内做一次，
    调用方不得预乘原始列——否则净流动性会错 1000 倍（历史 bug）。
    """
    df = read_macro_category(category)
    for partner in cross_category_partners(category):
        if _fred_csv_path(partner).exists():
            df = df.join(read_macro_category(partner), how="left")
    return derive_macro(df)


def load_macro_categories(categories: set[str]) -> pd.DataFrame:
    """加载多分类：所需分类 + 伙伴分类的并集，outer join 合并后一次派生。

    请求的分类缺失抛 FileNotFoundError；伙伴分类缺失则跳过（与单分类一致）。
    先并集再合并原始帧，避免逐分类 join 伙伴后列重复产生 _x/_y 后缀
    （BEI 等跨分类指标在旧实现里因此直接 404）。
    """
    cats = set(categories)
    if not cats:
        return pd.DataFrame()
    partners: set[str] = set()
    for cat in list(cats):
        partners.update(cross_category_partners(cat))
    frames = [read_macro_category(cat) for cat in sorted(cats)]
    for partner in sorted(partners - cats):
        if _fred_csv_path(partner).exists():
            frames.append(read_macro_category(partner))
    merged = frames[0]
    for df in frames[1:]:
        merged = merged.join(df, how="outer")
    merged.sort_index(inplace=True)
    return derive_macro(merged)


def rrp_in_millions(df: pd.DataFrame) -> pd.DataFrame:
    """RRPONTSYD 十亿美元 → 百万美元（显示层统一单位用）。

    derive_macro 的 NET_LIQUIDITY 公式内部已做同样的换算，调用方不得对
    derive 后的结果再乘——本函数只用于把 RRP 系列与 WALCL/WTREGEN 等
    百万单位系列同图展示（stacked / 卡片）的显示层。
    """
    out = df.copy()
    if "RRPONTSYD" in out.columns:
        out["RRPONTSYD"] = out["RRPONTSYD"] * 1000
    return out


def categories_for(name: str) -> list[str] | None:
    """返回计算某指标（原始或派生）所需加载的 FRED 分类；未知指标返回 None。

    派生指标按 DERIVED_INPUTS 反查输入列归属，原始指标按其所在分类。
    取代 server 旧的手写 _DERIVED_CATS / _METRIC_TO_CAT（同一知识的第四份拷贝）。
    """
    from src.config import FRED_SERIES

    inputs = set(DERIVED_INPUTS.get(name, (name,)))
    out: list[str] = []
    for cat, series_map in FRED_SERIES.items():
        if inputs & set(series_map):
            out.append(cat)
    return out or None
