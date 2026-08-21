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
import json
import logging
import requests
import db

logger = logging.getLogger(__name__)

BASE = "https://foreupsoftware.com"

# Bump when resolution changes in a way that makes previously saved rows wrong.
#   1 → original
#   2 → parses ForeUp's booking_classes array (older rows stored booking_class =
#       schedule_id, which ForeUp rejects), and captures online_open_time/timezone
RESOLVER_VERSION = 2

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

def detect_platform(url: str) -> str:
    """Detect which booking platform a URL belongs to."""
    url_lower = url.lower()
    if "teeitup.golf" in url_lower or "teeitup.com" in url_lower or "book.teeitup" in url_lower:
        return "teeitup"
    if "golfnow.com" in url_lower:
        return "golfnow"
    if "foreupsoftware.com" in url_lower or "foreup" in url_lower:
        return "foreup"
    # Could be a course website — fetch page and check for embedded platforms
    return "unknown"


def _is_current(saved: dict) -> bool:
    """
    Is a saved course row still trustworthy?

    Rows written before RESOLVER_VERSION 2 stored booking_class = schedule_id,
    which ForeUp rejects outright, and carry no online_open_time/timezone. The
    cache would otherwise shield those bad values forever.
    """
    try:
        return int(saved.get("resolver_version") or 0) >= RESOLVER_VERSION
    except (TypeError, ValueError):
        return False


def resolve_course_from_url(url: str, force: bool = False) -> dict:
    """
    Given a booking URL (ForeUp or GolfNow/TeeItUp), return:
      {course_id, schedule_id, booking_class, name, url, platform}

    Strategy:
      1. Detect platform
      2. Reuse a saved course, unless it predates the current resolver
      3. Parse IDs from the URL
      4. Fetch the page and scrape config if needed

    `force` re-detects even a current row, for the dashboard's re-detect button.
    """
    platform = detect_platform(url)
    clean_url = url.split("#")[0].rstrip("/")

    if platform in ("teeitup", "golfnow"):
        return _resolve_golfnow(clean_url, platform, force=force)

    # Unknown — fetch the page and look for embedded booking platform links
    if platform == "unknown":
        detected = _detect_from_page(clean_url)
        if detected:
            return detected
        raise ValueError(
            "Could not detect booking platform from this URL.\n\n"
            "Please paste the direct booking page URL instead:\n"
            "• ForeUp: https://foreupsoftware.com/index.php/booking/NNNNN\n"
            "• GolfNow: https://www.golfnow.com/tee-times/facility/NNNNN-course-name\n"
            "• TeeItUp: https://course-name.book.teeitup.golf/tee-times"
        )

    from foreup_client import parse_course_url

    # Step 1 — try basic parse first to get course_id
    try:
        basic = parse_course_url(clean_url)
        course_id = basic["course_id"]
    except Exception as e:
        raise ValueError(str(e))

    # Step 2 — check saved courses, unless the row predates the current resolver
    courses = db.load_courses()
    if course_id in courses and not force:
        saved = courses[course_id]
        if _is_current(saved):
            logger.info(f"Using saved course {course_id}: {saved.get('name')}")
            return saved
        logger.info(
            f"Saved course {course_id} was written by resolver v"
            f"{saved.get('resolver_version', 0)} (current v{RESOLVER_VERSION}) — "
            f"re-detecting."
        )

    # Step 3 — IDs supplied directly in the URL (e.g. pasted from DevTools) win
    # over anything scraped, but we still fetch the page below for the course
    # name, release time, and timezone. Short-circuiting here would produce a
    # row that cannot support snipe mode.
    url_schedule_id   = basic.get("schedule_id") if basic.get("booking_class") else None
    url_booking_class = basic.get("booking_class")

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

    # ForeUp embeds the real booking classes as a JSON array in the page. The old
    # regexes never matched it, so booking_class silently fell back to schedule_id
    # — which ForeUp rejects with a bare `false`.
    booking_classes = _extract_booking_classes(html)

    # A booking class carries the tee sheet it belongs to; that beats the regex.
    if booking_classes and booking_classes[0].get("teesheet_id"):
        schedule_id = booking_classes[0]["teesheet_id"]

    booking_class = _pick_booking_class(course_id, schedule_id, booking_classes)

    # IDs the caller supplied in the URL are explicit intent — honour them.
    if url_schedule_id:
        schedule_id = url_schedule_id
    if url_booking_class:
        booking_class = url_booking_class

    # Try to get the course name from the page title
    name_match = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    name = name_match.group(1).strip() if name_match else f"Course {course_id}"
    # Clean up common title suffixes — "Grapevine Golf Course - Online Booking"
    # should store as "Grapevine Golf Course" so it matches elsewhere.
    name = re.sub(
        r'\s*[-|–:]\s*(Online\s+Booking|Book\s+a\s+Tee\s+Time|ForeUp|Tee\s*Times?|Booking|Book).*$',
        '', name, flags=re.IGNORECASE).strip()
    if not name:
        name = f"Course {course_id}"

    if not schedule_id:
        raise RuntimeError(
            f"Could not auto-detect schedule_id from the booking page. "
            f"Please open the page in Chrome DevTools → Network tab, "
            f"click a date, and paste the tee times request URL instead."
        )

    if not booking_class:
        raise RuntimeError(
            f"Found schedule_id={schedule_id} but no usable booking_class for course "
            f"{course_id}. Open the booking page, pick your player category, and "
            f"enter the booking_class manually."
        )

    result = {
        "course_id": course_id,
        "schedule_id": schedule_id,
        "booking_class": booking_class,
        "name": name,
        "url": booking_url,
        "platform": "foreup",
        "booking_classes": booking_classes,
        "online_open_time": _open_time_for(booking_classes, booking_class),
        "timezone": _extract_timezone(html),
        "resolver_version": RESOLVER_VERSION,
    }

    save_course(course_id, result)
    logger.info(f"Auto-detected and saved course {course_id}: {result}")
    return result


# Booking platform fingerprints, most specific first. Order matters: a ForeUp
# booking link beats a stray GolfNow marketing asset on the same page.
PLATFORM_PATTERNS = (
    ("foreup",     r'foreupsoftware\.com/index\.php/booking/\d+'),
    ("teeitup",    r'[\w-]+\.book\.teeitup\.(?:golf|com)[^\s"\'<>]*'),
    ("teeitup",    r'book\.teeitup\.(?:golf|com)[^\s"\'<>]*'),
    ("golfnow",    r'golfnow\.com/tee-times/facility/\d+[^\s"\'<>]*'),
    # Recognised but not supported yet — naming them beats a bare "unknown".
    ("chronogolf", r'chronogolf\.com/(?:widgets?|club)/[\w/-]+'),
    ("chronogolf", r'chronogolfSettings|chronogolf-js'),
    ("teesnap",    r'[\w-]+\.teesnap\.net[^\s"\'<>]*'),
    ("quick18",    r'[\w-]+\.quick18\.com[^\s"\'<>]*'),
)

# Course sites usually link booking from a subpage rather than the homepage.
_BOOKING_LINK_RE = re.compile(
    r'href=["\']([^"\']{3,120})["\']', re.IGNORECASE)
_BOOKING_WORDS = ("tee-time", "tee_time", "teetime", "tee-times", "book", "reserv")


def _match_platform(html: str) -> dict | None:
    for platform, pattern in PLATFORM_PATTERNS:
        m = re.search(pattern, html, re.IGNORECASE)
        if not m:
            continue
        found = m.group(0)
        if found.startswith("http"):
            booking_url = found
        elif "." in found:               # a bare domain like foo.book.teeitup.com
            booking_url = f"https://{found}"
        else:                            # a JS marker, no usable URL
            booking_url = ""
        return {"platform": platform, "booking_url": booking_url}
    return None


def sniff_platform(url: str, timeout: int = 10, follow_links: int = 2) -> dict:
    """
    Identify which booking platform a course website uses, without touching the
    database or resolving IDs.

    Checks the landing page, then follows up to `follow_links` booking-looking
    links, since most course sites keep the widget on a /tee-times page.

    Returns {platform, booking_url}; platform is "unknown" if nothing matches.
    """
    direct = detect_platform(url)
    if direct != "unknown":
        return {"platform": direct, "booking_url": url}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        html = resp.text
        base = resp.url
    except Exception as e:
        logger.debug(f"sniff_platform could not fetch {url}: {e}")
        return {"platform": "unknown", "booking_url": ""}

    hit = _match_platform(html)
    if hit:
        return hit

    # Nothing on the landing page — try the pages a golfer would click.
    from urllib.parse import urljoin, urlparse
    origin = urlparse(base).netloc
    candidates, seen = [], set()
    for href in _BOOKING_LINK_RE.findall(html):
        low = href.lower()
        if not any(w in low for w in _BOOKING_WORDS):
            continue
        full = urljoin(base, href)
        # Stay on the course's own site; off-site links are handled by the
        # pattern match above.
        if urlparse(full).netloc != origin or full in seen:
            continue
        seen.add(full)
        candidates.append(full)
        if len(candidates) >= follow_links:
            break

    for link in candidates:
        try:
            sub = requests.get(link, headers=HEADERS, timeout=timeout)
            hit = _match_platform(sub.text)
            if hit:
                logger.info(f"Found {hit['platform']} for {url} via {link}")
                return hit
        except Exception as e:
            logger.debug(f"Could not fetch booking subpage {link}: {e}")

    return {"platform": "unknown", "booking_url": ""}


def _detect_from_page(url: str) -> dict:
    """
    Fetch a course website page and look for embedded booking platform links.
    Handles courses that embed ForeUp/TeeItUp/GolfNow widgets on their own site.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        html = resp.text

        # Look for TeeItUp embed
        m = re.search(r'teeitup\.golf[/\w-]*\?facilityId=(\d+)', html)
        if not m:
            m = re.search(r'book\.teeitup\.golf.*?facilityId=(\d+)', html)
        if not m:
            m = re.search(r'teeitup\.golf/([\w-]+)', html)
        if m:
            facility_id = m.group(1) if m.group(1).isdigit() else None
            # If we got a slug, try to extract from the full URL in the HTML
            teeitup_url_m = re.search(r"https?://[\w-]+\.book\.teeitup\.golf[^\s\"'<>]+", html)
            if teeitup_url_m:
                teeitup_url = teeitup_url_m.group(0)
                logger.info(f"Found TeeItUp URL on page: {teeitup_url}")
                return _resolve_golfnow(teeitup_url, "teeitup")
            if facility_id:
                return _resolve_golfnow(
                    f"https://book.teeitup.golf/tee-times?facilityId={facility_id}",
                    "teeitup"
                )

        # Look for GolfNow embed
        m = re.search(r'golfnow\.com/tee-times/facility/(\d+)', html)
        if m:
            return _resolve_golfnow(
                f"https://www.golfnow.com/tee-times/facility/{m.group(1)}",
                "golfnow"
            )

        # Look for ForeUp embed
        m = re.search(r'foreupsoftware\.com/index\.php/booking/(\d+)', html)
        if m:
            foreup_url = f"https://foreupsoftware.com/index.php/booking/{m.group(1)}"
            logger.info(f"Found ForeUp URL on page: {foreup_url}")
            # Recurse with the actual ForeUp URL
            return resolve_course_from_url(foreup_url)

    except Exception as e:
        logger.warning(f"Could not fetch page to detect platform: {e}")

    return None


def _resolve_golfnow(url: str, platform: str, force: bool = False) -> dict:
    """Resolve a GolfNow/TeeItUp course URL."""
    from golfnow_client import parse_golfnow_url
    info = parse_golfnow_url(url)
    facility_id = info["facility_id"]

    # Check saved courses
    courses = db.load_courses()
    if facility_id in courses and not force:
        saved = courses[facility_id]
        if _is_current(saved):
            logger.info(f"Using saved GolfNow course {facility_id}: {saved.get('name')}")
            return saved
        logger.info(f"Saved GolfNow course {facility_id} is stale — re-detecting.")

    # Try to get the course name from the URL itself first
    from urllib.parse import urlparse as _urlparse
    parsed_url = _urlparse(url)
    name = f"Course {facility_id}"  # fallback

    # GolfNow: /tee-times/facility/1307-pecan-hollow-golf-course -> "Pecan Hollow Golf Course"
    import re as _re
    gn_match = _re.search(r'/facility/\d+-(.+?)(?:/|$)', parsed_url.path)
    if gn_match:
        name = gn_match.group(1).replace("-", " ").title()

    # TeeItUp subdomain: "pecan-hollow-golf-course.book.teeitup.com" -> "Pecan Hollow Golf Course"
    elif ".book." in parsed_url.netloc:
        subdomain = parsed_url.netloc.split(".book.")[0]
        if subdomain and subdomain not in ("www", "book"):
            name = subdomain.replace("-", " ").title()

    # Fetch page once — reuse for both name and ObjectId extraction
    resp = None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        m = re.search(r'<title>([^<]+)</title>', resp.text, re.IGNORECASE)
        if m:
            raw_name = m.group(1).strip()
            # Strip trailing platform names
            cleaned = re.sub(r"\s*[-|–|:]\s*(GolfNow|TeeItUp|Tee Times|Book|Golf).*$", "", raw_name, flags=re.IGNORECASE).strip()
            # Only use page title if it's meaningfully longer than generic
            if cleaned and cleaned.lower() not in ("tee times", "book", "golf", "") and len(cleaned) > 5:
                name = cleaned
    except Exception as e:
        logger.warning(f"Could not fetch GolfNow page for name: {e}")
        resp = None

    # For TeeItUp, try to scrape the Kenna ObjectId from the page JS
    # The real API uses a 24-char hex ObjectId, not the numeric course param
    kenna_id = facility_id  # fallback to numeric id
    be_alias = ""
    if platform == "teeitup":
        try:
            if not resp:
                resp = requests.get(url, headers=HEADERS, timeout=15)
            # Look for the ObjectId in the page JS bundles/config
            m = re.search(r'"courseId"\s*:\s*"([a-f0-9]{24})"', resp.text)
            if not m:
                m = re.search(r'/course/([a-f0-9]{24})/', resp.text)
            if not m:
                m = re.search(r'"_id"\s*:\s*"([a-f0-9]{24})"', resp.text)
            if m:
                kenna_id = m.group(1)
                logger.info(f"Extracted Kenna ObjectId: {kenna_id}")
            # Extract subdomain slug for X-Be-Alias header
            from urllib.parse import urlparse as _up
            _parsed = _up(url)
            if ".book." in _parsed.netloc:
                be_alias = _parsed.netloc.split(".book.")[0]
        except Exception as e:
            logger.warning(f"Could not extract Kenna ObjectId: {e}")

    result = {
        "course_id":     kenna_id,      # Kenna ObjectId for the API
        "schedule_id":   kenna_id,
        "booking_class": "",
        "name":          name,
        "url":           url,
        "platform":      platform,
        "be_alias":      be_alias,      # subdomain slug for X-Be-Alias header
        "resolver_version": RESOLVER_VERSION,
    }
    db.save_course(kenna_id, result)
    logger.info(f"Saved GolfNow course {kenna_id}: {name} (alias={be_alias})")
    return result


def _extract_booking_classes(html: str) -> list[dict]:
    """
    Pull the `booking_classes` array out of the JSON blob ForeUp embeds in the
    booking page. Returns the bookable ones (active, not hidden), each with
    booking_class_id / teesheet_id / name / block_online_booking.
    """
    marker = '"booking_classes":'
    idx = html.find(marker)
    if idx == -1:
        return []

    start = html.find("[", idx)
    if start == -1:
        return []

    # Bracket-match to find the end of the array — it contains nested objects.
    depth, end = 0, -1
    for i in range(start, len(html)):
        c = html[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return []

    raw = html[start:end].replace("\\/", "/")
    try:
        classes = json.loads(raw)
    except Exception as e:
        logger.warning(f"Could not parse booking_classes JSON: {e}")
        return []

    bookable = [
        {
            "booking_class_id":     str(c.get("booking_class_id", "")),
            "teesheet_id":          str(c.get("teesheet_id", "")),
            "name":                 c.get("name", ""),
            "block_online_booking": str(c.get("block_online_booking", "0")) == "1",
            "online_open_time":     c.get("online_open_time") or "",
        }
        for c in classes
        if isinstance(c, dict)
        and str(c.get("active", "1")) == "1"
        and str(c.get("hidden", "0")) == "0"
        and c.get("booking_class_id")
    ]
    logger.info(
        "Found %d bookable booking class(es): %s",
        len(bookable),
        ", ".join(f"{c['name']}={c['booking_class_id']}" for c in bookable),
    )
    return bookable


def _pick_booking_class(course_id: str, schedule_id: str, classes: list[dict]) -> str:
    """
    Choose the booking class that actually returns tee times.

    Restricted classes (member/pass-holder) 401 without a matching login, so we
    probe each candidate read-only with a real availability request and take the
    first that returns a list. Public classes work unauthenticated.
    """
    if not classes:
        return ""
    if len(classes) == 1:
        return classes[0]["booking_class_id"]

    from foreup_client import probe_booking_class

    # Prefer names that read as open-to-all, but verify rather than trust.
    def openness(c: dict) -> int:
        n = c["name"].lower()
        if any(w in n for w in ("public", "guest", "non-member", "nonmember")):
            return 0
        return 1

    for c in sorted(classes, key=openness):
        try:
            if probe_booking_class(course_id, schedule_id, c["booking_class_id"]):
                logger.info(
                    f"Booking class {c['booking_class_id']} ({c['name']}) is usable "
                    f"without a login — using it."
                )
                return c["booking_class_id"]
            logger.info(f"Booking class {c['booking_class_id']} ({c['name']}) is restricted.")
        except Exception as e:
            logger.warning(f"Probe failed for booking class {c['booking_class_id']}: {e}")

    # Every class is restricted — fall back to the most open-sounding one and let
    # the authenticated poll sort it out.
    fallback = sorted(classes, key=openness)[0]["booking_class_id"]
    logger.info(f"No class worked unauthenticated; defaulting to {fallback}.")
    return fallback


def _open_time_for(classes: list[dict], booking_class: str) -> str:
    """The HHMM at which online booking opens for the chosen class, e.g. '0600'."""
    for c in classes:
        if c["booking_class_id"] == booking_class:
            return c.get("online_open_time") or ""
    return ""


def _extract_timezone(html: str) -> str:
    """
    The course's IANA timezone, so a release time can be anchored correctly
    regardless of what timezone the server runs in.
    """
    # The blob is JSON-escaped, so the value arrives as "America\/Chicago".
    for pattern in (r'"timezone"\s*:\s*"([^"]+)"', r'"time_zone"\s*:\s*"([^"]+)"'):
        m = re.search(pattern, html)
        if m:
            tz = m.group(1).replace("\\/", "/")
            if "/" in tz:
                return tz
    return ""


def _extract_id(html: str, patterns: list[str]) -> str:
    """Try each regex pattern and return first match."""
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""
