"""美东时区与「现在」的唯一来源。

美股相关的日期键、交易时段判定、快照文件名一律从这里取：
本机在 UTC+8 的傍晚正好是美东同一天的下午到收盘后，直接用本机时间
会把当晚的数据记成第二天。
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    """当前美东时间（带时区）。"""
    return datetime.now(timezone.utc).astimezone(ET)


def et_today() -> date:
    """当前美东日期（逐日快照的日期键）。"""
    return now_et().date()
