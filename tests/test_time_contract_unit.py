"""时间口径与会话关闭的纯单元回归。"""

from datetime import date, datetime, timedelta, timezone

from app.services.ai_lab import realtime_session
from app.utils.time import (
    business_day_range_utc_naive,
    business_local_to_utc_naive,
    to_iso,
)


def test_shanghai_late_night_is_previous_utc_day():
    local = datetime(2026, 10, 21, 23, 30)

    utc = business_local_to_utc_naive(local)

    assert utc == datetime(2026, 10, 21, 15, 30)
    assert to_iso(utc) == "2026-10-21T15:30:00Z"


def test_business_day_range_is_half_open_utc():
    start, end = business_day_range_utc_naive(date(2026, 10, 21))

    assert start == datetime(2026, 10, 20, 16, 0)
    assert end == datetime(2026, 10, 21, 16, 0)
    assert end - start == timedelta(days=1)


def test_delayed_removal_does_not_delete_replacement_session():
    sid = "time-contract-sid"
    first = realtime_session.get_session(sid)
    realtime_session.remove_session(sid, expected=realtime_session.VideoCallSession(sid=sid))
    assert realtime_session.get_session(sid) is first

    realtime_session.remove_session(sid, expected=first)
    replacement = realtime_session.get_session(sid)
    assert replacement is not first

    # 旧清理任务拿着 first，不能删掉同一 sid 上已经建立的新会话。
    realtime_session.remove_session(sid, expected=first)
    assert realtime_session.get_session(sid) is replacement

    realtime_session.remove_session(sid, expected=replacement)
    assert not realtime_session.has_session(sid)


def test_to_iso_preserves_aware_offset():
    value = datetime(2026, 10, 21, 23, 30, tzinfo=timezone(timedelta(hours=8)))
    assert to_iso(value) == "2026-10-21T23:30:00+08:00"
