"""时间工具。

数据库中的所有“具体时刻”统一存 naive UTC；教练排班、月历、打卡日期等
“业务日期”统一按 Asia/Shanghai 解释。两类值不能直接混用。
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def utcnow_naive() -> datetime:
    """MySQL DATETIME 无时区，统一返回 naive UTC。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def business_now() -> datetime:
    """当前上海业务时间（带时区）。"""
    return datetime.now(BUSINESS_TIMEZONE)


def business_now_naive() -> datetime:
    """当前上海业务墙上时间，仅用于与 date + time 排班比较。"""
    return business_now().replace(tzinfo=None)


def business_today() -> date:
    """当前上海业务日期。"""
    return business_now().date()


def to_iso(dt: datetime | None) -> str | None:
    """输出 ISO 8601。

    数据库 DATETIME 是 naive UTC，因此补 ``Z``；带时区的值按自身偏移输出。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return dt.isoformat()


def business_local_to_utc_naive(value: datetime) -> datetime:
    """把上海业务墙上时间转换为 naive UTC。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=BUSINESS_TIMEZONE)
    else:
        value = value.astimezone(BUSINESS_TIMEZONE)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def business_day_start_utc_naive(day: date) -> datetime:
    """上海某个自然日起点对应的 naive UTC。"""
    return business_local_to_utc_naive(datetime.combine(day, time.min))


def business_day_range_utc_naive(day: date) -> tuple[datetime, datetime]:
    """上海某个自然日的半开区间 ``[开始, 下一日开始)``，值为 naive UTC。"""
    start = business_day_start_utc_naive(day)
    return start, business_day_start_utc_naive(day + timedelta(days=1))


def business_month_range_utc_naive(year: int, month: int) -> tuple[date, datetime, datetime]:
    """返回上海某月首日、以及该月对应的 UTC 半开区间。"""
    start_day = date(year, month, 1)
    next_day = (start_day + timedelta(days=32)).replace(day=1)
    return (
        start_day,
        business_day_start_utc_naive(start_day),
        business_day_start_utc_naive(next_day),
    )


def utc_naive_to_business(value: datetime) -> datetime:
    """把 naive UTC 转为带时区的上海业务时间。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BUSINESS_TIMEZONE)
