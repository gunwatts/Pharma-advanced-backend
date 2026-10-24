from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils import timezone


def business_tz():
    from apps.core.models import SystemSettings

    name = SystemSettings.load().business_timezone or settings.BUSINESS_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(settings.BUSINESS_TIMEZONE)


def business_now():
    return timezone.now().astimezone(business_tz())


def business_today() -> date:
    return business_now().date()


def to_business(dt: datetime):
    return dt.astimezone(business_tz()) if dt else None
