"""
Purpose Golf HTTP client.

Purpose Golf (Sherrill Park, and other Golf North Texas courses) runs its own
booking site. Endpoint confirmed via browser DevTools on
booking.purposegolf.com/courses/SherrillParkCourse2/3/teetimes:

  GET /api/courses/{course_id}/teeTimes   → every open tee time in the rolling
                                            booking window (about a week), no
                                            login, no date parameter
"""

import re
import logging
from datetime import datetime

from golfnow_client import new_session

logger = logging.getLogger(__name__)

BASE = "https://booking.purposegolf.com"

_URL_RE = re.compile(r"purposegolf\.com/courses/([\w-]+)/(\d+)", re.IGNORECASE)


def parse_purposegolf_url(url: str) -> dict:
    """
    Extract the slug and numeric course id from a Purpose Golf booking URL:
      https://booking.purposegolf.com/courses/SherrillParkCourse2/3/teetimes
    """
    m = _URL_RE.search(url)
    if not m:
        raise ValueError(
            "Could not read a Purpose Golf course from that URL. Paste the tee times "
            "page, e.g. https://booking.purposegolf.com/courses/SherrillParkCourse2/3/teetimes"
        )
    slug, course_id = m.group(1), m.group(2)
    return {
        "course_id": course_id,
        "slug":      slug,
        "url":       f"{BASE}/courses/{slug}/{course_id}/teetimes",
    }


class PurposeGolfClient:
    def __init__(self):
        # Same Cloudflare front door as GolfNow — use the Chrome-impersonating session.
        self.session = new_session()

    def fetch_tee_times(
        self,
        course_id: str,
        schedule_id: str,          # unused, kept for interface compatibility
        date: str,                 # "MM-DD-YYYY"
        time_from: str,            # "HH:MM"
        time_to: str,              # "HH:MM"
        players: int = 2,
        holes: int = 18,           # the feed doesn't say; every slot is a tee time
        booking_class: str = "",   # unused
        **kwargs,
    ) -> list[dict]:
        target = datetime.strptime(date, "%m-%d-%Y").strftime("%Y-%m-%d")

        resp = self.session.get(f"{BASE}/api/courses/{course_id}/teeTimes", timeout=15)
        if resp.status_code in (401, 403):
            raise PermissionError(f"Purpose Golf refused the request (HTTP {resp.status_code}).")
        if not resp.ok:
            raise RuntimeError(f"Purpose Golf failed: HTTP {resp.status_code} – {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"Purpose Golf returned non-JSON: {resp.text[:200]!r}")
        if not isinstance(data, list):
            raise ValueError(f"Unexpected Purpose Golf response: {str(data)[:200]}")

        all_times = [_normalize(item) for item in data if isinstance(item, dict)]
        on_date = [s for s in all_times if s["time"].startswith(target)]

        from_min = _time_to_minutes(time_from)
        to_min   = _time_to_minutes(time_to)
        filtered = [
            s for s in on_date
            if from_min <= _time_to_minutes(s["time"][11:16]) <= to_min
            and s["available_spots"] >= players
            and not s["_raw"].get("Inactive")
        ]

        logger.info(
            f"Purpose Golf: {len(all_times)} times in the window feed for course "
            f"{course_id}, {len(on_date)} on {date}, {len(filtered)} in "
            f"{time_from}–{time_to} with {players}+ spots"
        )
        return filtered

    @staticmethod
    def booking_url(course_url: str) -> str:
        """The course's tee times page; the feed has no per-date deep link."""
        return course_url


def _normalize(item: dict) -> dict:
    """Map a feed entry to the slot shape the rest of the app uses."""
    # "2026-10-09T08:45:00" → "2026-10-09 08:45", matching ForeUp/GolfNow slots.
    raw_time = str(item.get("Time") or "")
    return {
        "time":            raw_time[:16].replace("T", " "),
        "available_spots": int(item.get("AvailableGolfers") or 0),
        "green_fee":       (item.get("Rate") or 0) / 100,
        "holes":           18,
        "rate_type":       "",
        "_raw":            item,
    }


def _time_to_minutes(t: str) -> int:
    h, m = map(int, t.strip().split(":"))
    return h * 60 + m
