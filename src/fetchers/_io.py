"""
共享 I/O 工具 — 各 fetcher 的 __main__ 块复用。
将 DataPoint 列表保存为日频增量 CSV，pandas-ready。
"""

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd

from .quality import DataPoint, QAStatus


def save_daily_csv(
    filepath: Path,
    results: list[DataPoint],
    *,
    date_col: str = "date",
) -> None:
    """
    将 DataPoint 列表追加为 CSV 的一行。
    - 文件不存在时自动创建，首列 date_col + 各 metric 列
    - 已存在时追加行，同日期行会被覆盖（按 date_col 去重）
    - 仅保存 QAStatus.OK 的数据点，失败的不写入
    """
    # 构建行: {metric: value}
    today = datetime.now().strftime("%Y-%m-%d")
    row = {date_col: today}
    for dp in results:
        if dp.qa_status == QAStatus.OK and dp.value is not None:
            row[dp.metric] = dp.value
            if dp.volume is not None:
                row[f"{dp.metric}_volume"] = dp.volume

    if len(row) <= 1:
        return  # nothing to save

    # 收集所有列名（历史列 + 新列）
    columns = _load_columns(filepath)
    all_columns = list(dict.fromkeys(columns + [k for k in row if k not in columns]))

    # 读取已有行
    existing = _load_rows(filepath)

    # 去重：移除同日期的旧行
    existing = [r for r in existing if r.get(date_col) != today]
    existing.append(row)

    # 写回
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_columns)
        writer.writeheader()
        writer.writerows(existing)


def _load_columns(filepath: Path) -> list[str]:
    if not filepath.exists():
        return []
    with open(filepath, encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            return next(reader)
        except StopIteration:
            return []


def _load_rows(filepath: Path) -> list[dict]:
    if not filepath.exists():
        return []
    with open(filepath, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_timeseries(filepath: Path) -> pd.DataFrame:
    """统一读法：date 列解析为索引；文件不存在、空文件或损坏返回空 DataFrame。"""
    if not filepath.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(filepath, parse_dates=["date"])
    except Exception:
        return pd.DataFrame()  # 损坏 CSV 视为无数据，调用方重新拉取
    if df.empty:
        return pd.DataFrame()
    return df.set_index("date")


def upsert_timeseries(
    filepath: Path,
    df: pd.DataFrame,
    backfill: bool = False,
    column_order: list[str] | None = None,
) -> None:
    """将完整时间序列 DataFrame upsert 进 CSV（index=观测日期）。

    同日期行以新数据覆盖旧值，新日期追加；列取并集，新数据缺失处保留旧值。
    backfill=True 时全量覆盖（清旧格式 junk），否则为 upsert。
    column_order: 稳定列序（如 config.fred_series[category] 的键序）。
    系列瞬时拉取失败时 combine_first 会把 old-only 列追加到列尾，若不重排，
    整文件列序会来回 churn、污染 git 历史（审计 F-10）；其余列按字母序
    固定在尾部（BGCR 等外部合并列）。内容与磁盘完全一致（值/列序/索引，
    NaN 视为相等）时跳过写盘，避免定时任务每日整文件重写。
    与 save_daily_csv（拉取日快照）不同：此处 date 是观测日，适合月频/日频时间序列。
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if df.empty:
        return  # 空数据不写文件（与旧 save_data 一致）
    new = df.copy()
    new.index = new.index.astype(str)

    old: pd.DataFrame | None = None
    if backfill or not filepath.exists():
        combined = new
    else:
        old = pd.read_csv(filepath, index_col=0)
        old.index = old.index.astype(str)
        # new 优先：new 的非空值覆盖 old，new 为空处保留 old
        combined = new.combine_first(old)

    combined = combined.sort_index()
    # 列序归一化：primary 顺序 + 其余按字母序，消除瞬时缺列导致的列序 churn
    if column_order:
        primary = [c for c in column_order if c in combined.columns]
        rest = sorted(c for c in combined.columns if c not in column_order)
        combined = combined[primary + rest]

    # 内容未变（字符串化比较，含列序/索引；NaN→'nan' 一致）→ 跳过写盘
    if old is not None:
        if old.astype(str).equals(combined.astype(str)):
            return

    combined.to_csv(filepath, index_label="date")
