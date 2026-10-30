"""美东时钟唯一来源：本机 UTC+8 的傍晚落在美东当天，日期键必须按美东取。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.clock import ET, et_today, now_et


def test_now_et_is_new_york():
    now = now_et()
    assert now.tzinfo is not None
    assert now.utcoffset() == now.astimezone(ZoneInfo("America/New_York")).utcoffset()
    assert now.date() == datetime.now(ZoneInfo("America/New_York")).date()


def test_et_today_follows_now_et():
    assert et_today() == now_et().date()
    assert ET.key == "America/New_York"
