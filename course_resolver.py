"""
Auto-detects schedule_id and booking_class from a ForeUp booking page.

ForeUp embeds course config in the page HTML as a JavaScript object, e.g.:
  scheduleId: 1832
  bookingClass: 12800
  var booking_class = 12800;

We fetch the booking page and extract these with regex.
Resolved courses are saved to courses.json so users only set up each course once.
"""

import re
import logging
import requests
import db

logger = logging.getLogger(__name__)

BASE = "https://foreupsoftware.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── Course library — delegates to db.py ───────────────────────────────────────

def load_courses() -> dict:
    return db.load_courses()

def save_course(course_id: str, info: dict):
    db.save_course(course_id, info)

def delete_course(course_id: str):
    db.delete_course(course_id)


# ── Auto-detection ─────────────────────────────────────────────────────────────

def resolve_course_from_url(url: str) -> dict:
    """
    Given a ForeUp booking URL, return:
      {course_id, schedule_id, booking_class, name, url}

    Strategy:
      1. Check saved courses first
      2. Parse any IDs already in the URL/query string
      3. Fetch the booking page and scrape the JS config
    """
    from foreup_client import parse_course_url

    # Clean up the URL
    clean_url = url.split("#")[0].rstrip("/")

    # Step 1 — try basic parse first to get course_id
    try:
        basic = parse_course_url(clean_url)
        course_id = basic["course_id"]
    except Exception as e:
        raise ValueError(str(e))

    # Step 2 — check saved courses
    courses = db.load_courses()
    if course_id in courses:
        saved = courses[course_id]
        logger.info(f"Using saved course {course_id}: {saved.get('name')}")
        return saved

    # Step 3 — if URL already has schedule_id and booking_class, use them
    if basic.get("schedule_id") and basic.get("booking_class"):
        result = {
            "course_id": course_id,
            "schedule_id": basic["schedule_id"],
            "booking_class": basic["booking_class"],
            "name": f"Course {course_id}",
            "url": f"{BASE}/index.php/booking/{course_id}",
        }
        save_course(course_id, result)
        return result

    # Step 4 — fetch the booking page and scrape the config
    booking_url = f"{BASE}/index.php/booking/{course_id}"
    logger.info(f"Fetching booking page to auto-detect IDs: {booking_url}")

    try:
        resp = requests.get(booking_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        raise RuntimeError(
            f"Could not fetch the booking page ({e}). "
            f"Please enter schedule_id and booking_class manually."
        )

    schedule_id = _extract_id(html, [
        r'"schedule_id"\s*[=:]\s*["\']?(\d+)',
        r'scheduleId\s*[=:]\s*["\']?(\d+)',
        r'schedule_id\s*=\s*["\']?(\d+)',
        r'"schedule"\s*:\s*["\']?(\d+)',
        r'data-schedule[_-]id=["\'](\d+)',
    ])

    booking_class = _extract_id(html, [
        r'"booking_class"\s*[=:]\s*["\']?(\d+)',
        r'bookingClass\s*[=:]\s*["\']?(\d+)',
        r'booking_class\s*=\s*["\']?(\d+)',
        r'data-booking[_-]class=["\'](\d+)',
    ])

    # Try to get the course name from the page title
    name_match = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    name = name_match.group(1).strip() if name_match else f"Course {course_id}"
    # Clean up common suffixes
    name = re.sub(r'\s*[-|–]\s*(ForeUp|Tee Times|Book).*', '', name, flags=re.IGNORECASE).strip()
    if not name:
        name = f"Course {course_id}"

    if not schedule_id:
        raise RuntimeError(
            f"Could not auto-detect schedule_id from the booking page. "
            f"Please open the page in Chrome DevTools → Network tab, "
            f"click a date, and paste the tee times request URL instead."
        )

    # booking_class often equals schedule_id if not found separately
    if not booking_class:
        booking_class = schedule_id
        logger.warning(f"Could not find booking_class, defaulting to schedule_id={schedule_id}")

    result = {
        "course_id": course_id,
        "schedule_id": schedule_id,
        "booking_class": booking_class,
        "name": name,
        "url": booking_url,
    }

    save_course(course_id, result)
    logger.info(f"Auto-detected and saved course {course_id}: {result}")
    return result


def _extract_id(html: str, patterns: list[str]) -> str:
    """Try each regex pattern and return first match."""
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""
