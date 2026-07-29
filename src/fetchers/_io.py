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


def upsert_timeseries(filepath: Path, df: pd.DataFrame) -> None:
    """将完整时间序列 DataFrame upsert 进 CSV（index=观测日期）。

    同日期行以新数据覆盖旧值，新日期追加；列取并集，新数据缺失处保留旧值。
    与 save_daily_csv（拉取日快照）不同：此处 date 是观测日，适合月频/日频时间序列。
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    new = df.copy()
    new.index = new.index.astype(str)

    if filepath.exists():
        old = pd.read_csv(filepath, index_col=0)
        old.index = old.index.astype(str)
        # new 优先：new 的非空值覆盖 old，new 为空处保留 old
        combined = new.combine_first(old)
    else:
        combined = new

    combined = combined.sort_index()
    combined.to_csv(filepath, index_label="date")
