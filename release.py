"""
Works out the moment a course's tee sheet opens for a given date.

Courses release inventory on a rolling window: Grapevine's public class opens at
06:00 America/Chicago exactly 6 days ahead, so a Sunday round becomes bookable at
06:00 the Monday before. The scheduler needs that as an absolute UTC instant,
because the server does not run in the course's timezone.
"""

import logging
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Python < 3.9
    ZoneInfo = None

logger = logging.getLogger(__name__)

# How many days ahead each booking class can reserve. ForeUp exposes an
# days_in_advance field but leaves it null for most courses, so this is the
# observed default; override per course when a course proves different.
DEFAULT_DAYS_IN_ADVANCE = 6


def compute_release(
    target_date: str,          # "MM-DD-YYYY"
    online_open_time: str,     # "0600"
    course_timezone: str,      # "America/Chicago"
    days_in_advance: int = DEFAULT_DAYS_IN_ADVANCE,
) -> datetime | None:
    """
    Return the UTC instant the sheet for `target_date` opens, or None if there
    is not enough information to say.
    """
    if not online_open_time or len(online_open_time) != 4 or not online_open_time.isdigit():
        return None

    try:
        target = datetime.strptime(target_date, "%m-%d-%Y")
    except ValueError:
        logger.warning(f"Could not parse target_date {target_date!r}")
        return None

    # Refuse rather than guess: silently falling back to UTC would arm the snipe
    # hours off, which is worse than not arming it at all.
    tz = _zone(course_timezone)
    if tz is None:
        logger.warning(
            f"Cannot resolve course timezone {course_timezone!r} — refusing to "
            f"compute a release time."
        )
        return None

    hour, minute = int(online_open_time[:2]), int(online_open_time[2:])

    release_local = (target - timedelta(days=days_in_advance)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return release_local.replace(tzinfo=tz).astimezone(timezone.utc)


def _zone(name: str):
    """Resolve an IANA name to a tzinfo, or None if it cannot be resolved."""
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception as e:
        # Usually a missing tzdata package on a slim image.
        logger.warning(f"Unknown timezone {name!r}: {e}")
        return None


def describe(release_utc: datetime, course_timezone: str) -> str:
    """Human-readable release time in the course's own timezone."""
    local = release_utc.astimezone(_zone(course_timezone) or timezone.utc)
    return (f"{local.strftime('%a %b')} {local.day} at "
            f"{local.strftime('%I:%M %p').lstrip('0')} {local.tzname()}")
