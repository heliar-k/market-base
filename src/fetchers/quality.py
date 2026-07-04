"""
Unified data quality tracker for all fetched indicators.
Each indicator gets: value, as_of timestamp, source, qa_status.
"""

from dataclasses import asdict, dataclass
from enum import Enum


class QAStatus(str, Enum):
    PENDING = "pending"
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


@dataclass
class DataPoint:
    """A single data point with quality metadata."""

    metric: str
    value: float | None = None
    as_of: str | None = None  # ISO-8601 date string
    source: str = ""
    formula: str = ""
    qa_status: QAStatus = QAStatus.PENDING
    error_msg: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["qa_status"] = self.qa_status.value
        return d

    def mark_ok(self):
        self.qa_status = QAStatus.OK

    def mark_warn(self, msg: str = ""):
        self.qa_status = QAStatus.WARN
        self.error_msg = msg

    def mark_error(self, msg: str = ""):
        self.qa_status = QAStatus.ERROR
        self.error_msg = msg
