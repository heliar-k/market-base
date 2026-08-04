"""
Unified data quality tracker for all fetched indicators.
Each indicator gets: value, qa_status.
"""

from dataclasses import dataclass
from enum import Enum


class QAStatus(str, Enum):
    PENDING = "pending"
    OK = "ok"
    ERROR = "error"


@dataclass
class DataPoint:
    """A single data point with quality metadata."""

    metric: str
    value: float | None = None
    volume: float | None = None  # 日频快照（yfinance）附带成交量，其余源为 None
    qa_status: QAStatus = QAStatus.PENDING

    def mark_ok(self):
        self.qa_status = QAStatus.OK

    def mark_error(self):
        self.qa_status = QAStatus.ERROR
