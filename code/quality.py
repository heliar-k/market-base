"""
Unified data quality tracker for all fetched indicators.
Each indicator gets: value, as_of timestamp, source, qa_status.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class QAStatus(str, Enum):
    PENDING = "pending"
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


@dataclass
class DataPoint:
    """A single data point with quality metadata."""

    metric: str
    value: Optional[float] = None
    as_of: Optional[str] = None  # ISO-8601 date string
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


class QualityReport:
    """Collection of DataPoints with aggregate QA summary."""

    def __init__(self):
        self.points: list[DataPoint] = []
        self.generated_at: str = ""

    def add(self, dp: DataPoint):
        self.points.append(dp)

    def get(self, metric: str) -> Optional[DataPoint]:
        for dp in self.points:
            if dp.metric == metric:
                return dp
        return None

    def summary(self) -> dict:
        """Return aggregate QA summary."""
        total = len(self.points)
        ok_count = sum(1 for p in self.points if p.qa_status == QAStatus.OK)
        warn_count = sum(1 for p in self.points if p.qa_status == QAStatus.WARN)
        err_count = sum(1 for p in self.points if p.qa_status == QAStatus.ERROR)
        blocked = err_count > 0  # data quality block
        return {
            "total": total,
            "ok": ok_count,
            "warn": warn_count,
            "error": err_count,
            "blocked": blocked,
            "generated_at": self.generated_at,
        }

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "summary": self.summary(),
            "indicators": [p.to_dict() for p in self.points],
        }

    def mark_generated(self):
        self.generated_at = datetime.now(timezone.utc).isoformat()

    def save(self, path: str):
        self.mark_generated()
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
