"""
GolfNow / TeeItUp client.

GolfNow powers booking at thousands of courses via their TeeItUp platform.
Booking URLs look like:
  https://www.golfnow.com/tee-times/facility/12345-course-name
  https://COURSE-NAME.book.teeitup.golf/tee-times
  https://www.teeitup.com/tee-times?facilityId=12345

No login required to fetch available tee times — GolfNow's API is public.
Endpoints discovered via DevTools on teeitup.golf booking pages.
"""

import re
import logging
import requests
from datetime import datetime
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# GolfNow public API base
GOLFNOW_API = "https://api.golfnow.com/v1"
TEEITUP_API  = "https://api2.teeitup.golf/api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.golfnow.com",
    "Referer": "https://www.golfnow.com/",
}


def parse_golfnow_url(url: str) -> dict:
    """
    Extract facility_id and platform from a GolfNow/TeeItUp URL.

    Supported formats:
      https://www.golfnow.com/tee-times/facility/12345-course-name
      https://course-name.book.teeitup.golf/tee-times
      https://www.teeitup.com/tee-times?facilityId=12345
      https://book.teeitup.golf/tee-times?courseId=12345
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    facility_id = None
    platform = "golfnow"

    # GolfNow facility URL: /tee-times/facility/12345-name
    m = re.search(r'/facility/(\d+)', url)
    if m:
        facility_id = m.group(1)
        platform = "golfnow"

    # TeeItUp: course-name.book.teeitup.golf or course-name.book.teeitup.com
    elif "teeitup.golf" in parsed.netloc or "teeitup.com" in parsed.netloc:
        platform = "teeitup"
        # Try all known query param names including 'course'
        facility_id = (
            qs.get("facilityId", [None])[0] or
            qs.get("courseId", [None])[0] or
            qs.get("facility_id", [None])[0] or
            qs.get("course", [None])[0]
        )
        # Try path: /tee-times/12345
        if not facility_id:
            m = re.search(r'/(\d{4,})', parsed.path)
            if m:
                facility_id = m.group(1)

    if not facility_id:
        raise ValueError(
            "Could not extract facility ID from URL. "
            "Expected formats:\n"
            "  https://www.golfnow.com/tee-times/facility/12345-course-name\n"
            "  https://course-name.book.teeitup.golf/tee-times?facilityId=12345"
        )

    return {
        "facility_id": facility_id,
        "platform": platform,
        "course_id": facility_id,      # alias used by the rest of the app
        "schedule_id": facility_id,    # not used for GolfNow but keeps interface consistent
        "booking_class": "",
    }


class GolfNowClient:
    """
    Fetches tee times from GolfNow / TeeItUp.
    No authentication required for public courses.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def fetch_tee_times(
        self,
        course_id: str,
        schedule_id: str,          # unused, kept for interface compatibility
        date: str,                 # "MM-DD-YYYY"
        time_from: str,            # "HH:MM"
        time_to: str,              # "HH:MM"
        players: int = 2,
        holes: int = 18,
        booking_class: str = "",   # unused
        platform: str = "teeitup",
        **kwargs,
    ) -> list[dict]:
        """Fetch available tee times and filter to the requested window."""

        # Convert date MM-DD-YYYY → YYYY-MM-DD for GolfNow API
        try:
            d = datetime.strptime(date, "%m-%d-%Y")
            api_date = d.strftime("%Y-%m-%d")
        except ValueError:
            api_date = date

        if platform == "teeitup":
            # be_alias is the subdomain slug for X-Be-Alias header
            be_alias = kwargs.get("be_alias", "")
            all_times = self._fetch_teeitup(course_id, api_date, players, holes, be_alias=be_alias)
        else:
            all_times = self._fetch_golfnow(course_id, api_date, players, holes)

        # Filter by time window
        from_min = _time_to_minutes(time_from)
        to_min   = _time_to_minutes(time_to)

        filtered = []
        for slot in all_times:
            slot_min = _parse_slot_time(slot.get("time", ""))
            if slot_min is not None and from_min <= slot_min <= to_min:
                filtered.append(slot)

        logger.info(
            f"GolfNow: fetched {len(all_times)} times for facility {course_id} "
            f"on {date}, {len(filtered)} in window {time_from}–{time_to}"
        )
        return filtered

    def _fetch_teeitup(
        self,
        facility_id: str,
        date: str,
        players: int,
        holes: int,
        be_alias: str = "",
    ) -> list[dict]:
        """
        Fetch from TeeItUp (powered by Kenna/Lightspeed Golf).

        Confirmed endpoint (from DevTools):
          GET https://phx-api-be-east-1b.kenna.io/course/{objectId}/tee-time/locks?localDate=YYYY-MM-DD
          Headers: X-Be-Alias: {subdomain-slug}  (e.g. "pecan-hollow-golf-course")
                   Origin/Referer: https://{slug}.book.teeitup.golf

        facility_id should be the 24-char hex Kenna ObjectId, stored during course resolution.
        be_alias is the subdomain slug used in X-Be-Alias header.
        """
        kenna_base = "https://phx-api-be-east-1b.kenna.io"
        alias = be_alias or facility_id

        kenna_headers = {
            **HEADERS,
            "Origin":     f"https://{alias}.book.teeitup.golf",
            "Referer":    f"https://{alias}.book.teeitup.golf/",
            "X-Be-Alias": alias,
        }

        url = f"{kenna_base}/course/{facility_id}/tee-time/locks"
        params = {"localDate": date}

        resp = self.session.get(url, params=params, headers=kenna_headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        slots = self._normalize_kenna(data)
        logger.info(f"TeeItUp/Kenna: got {len(slots)} slots from {url}")
        return slots

    def _fetch_golfnow(self, facility_id: str, date: str, players: int, holes: int) -> list[dict]:
        """
        Fetch from GolfNow.
        Confirmed endpoint + payload from DevTools:
          POST https://www.golfnow.com/api/tee-times/tee-time-search-results
          Body: JSON with facilityId, date (formatted "Mar 21 2026"), players, timeMin/timeMax, etc.
        timeMin/timeMax are in 30-min increments from midnight (10=5am, 42=9pm).
        """
        url = "https://www.golfnow.com/api/tee-times/tee-time-search-results"

        # Convert YYYY-MM-DD to "Mar 21 2026" format GolfNow expects
        from datetime import datetime as _dt
        try:
            d = _dt.strptime(date, "%Y-%m-%d")
            gn_date = d.strftime("%b %-d %Y")  # e.g. "Mar 20 2026"
        except Exception:
            gn_date = date

        payload = {
            "address":                  None,
            "bestDealsOnly":            False,
            "currentClientDate":        _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "customerToken":            None,
            "date":                     gn_date,
            "daysToSearch":             None,
            "excludeFeaturedFacilities": True,
            "excludePrivateFacilities": False,
            "facilityGroupId":          None,
            "facilityId":               int(facility_id) if facility_id.isdigit() else facility_id,
            "facilityIds":              [],
            "facilityType":             "Any",
            "golfPassPerksOnly":        False,
            "holes":                    "Any",
            "hotDealsOnly":             False,
            "latitude":                 None,
            "longitude":                None,
            "pageNumber":               0,
            "pageSize":                 30,
            "players":                  0,  # 0 = any
            "priceMax":                 10000,
            "priceMin":                 0,
            "rateType":                 "all",
            "searchType":               "Facility",
            "sortBy":                   "Date",
            "sortByRollup":             "Date.MinDate",
            "sortDirection":            0,
            "teeTimeCount":             15,
            "timeMax":                  42,  # 9pm
            "timeMin":                  10,  # 5am
            "timePeriod":               "Any",
            "trackmanOnly":             False,
            "useWidgetNextAvailableDays": None,
            "view":                     "Grouping",
        }

        headers = {
            **HEADERS,
            "Accept":       "application/json",
            "Content-Type": "application/json",
            "Origin":       "https://www.golfnow.com",
            "Referer":      f"https://www.golfnow.com/tee-times/facility/{facility_id}/search",
        }

        resp = self.session.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        if not resp.text.strip():
            raise ValueError(f"GolfNow returned empty response (status {resp.status_code})")
        data = resp.json()
        return self._normalize_golfnow(data)

    def _normalize_teeitup(self, data) -> list[dict]:
        """Normalize TeeItUp response to our standard slot format."""
        slots = []
        # TeeItUp wraps in various structures
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = (data.get("teeTimes") or data.get("tee_times") or
                     data.get("data") or data.get("results") or [])

        for item in items:
            time_str = (item.get("time") or item.get("teeTime") or
                        item.get("startTime") or item.get("start_time") or "")
            slots.append({
                "time":             time_str,
                "available_spots":  item.get("availableSpots") or item.get("available_spots") or item.get("openSlots") or 0,
                "green_fee":        item.get("greenFee") or item.get("green_fee") or item.get("price") or 0,
                "holes":            item.get("holes") or 18,
                "rate_type":        item.get("rateType") or item.get("rate_type") or "",
                "_raw":             item,
            })
        return slots

    def _normalize_kenna(self, data) -> list[dict]:
        """
        Normalize Kenna/TeeItUp response.
        Confirmed response shape from DevTools:
          The /course/{id}/tee-time/locks endpoint returns a list of lock objects.
        """
        slots = []
        # Response may be a list directly or wrapped in a key
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = (data.get("teeTimes") or data.get("locks") or
                     data.get("data") or data.get("results") or [])

        for item in items:
            # Extract time — try various field names
            time_str = (
                item.get("localTime") or item.get("startTime") or
                item.get("time") or item.get("localStartTime") or
                item.get("teeTime") or ""
            )
            # If time is just HH:MM, prefix with date
            if time_str and len(time_str) <= 8 and ":" in time_str:
                date_val = item.get("localDate") or item.get("date") or ""
                if date_val:
                    time_str = f"{date_val} {time_str}"

            spots = (
                item.get("availableSpots") or item.get("available_spots") or
                item.get("openSpots") or item.get("maxPlayers") or
                item.get("spotsAvailable") or 0
            )
            fee = (
                item.get("greenFee") or item.get("green_fee") or
                item.get("price") or item.get("rate") or 0
            )
            slots.append({
                "time":            time_str,
                "available_spots": spots,
                "green_fee":       fee,
                "holes":           item.get("holes") or 18,
                "rate_type":       item.get("rateType") or "",
                "_raw":            item,
            })
        return slots

    def _normalize_golfnow(self, data) -> list[dict]:
        """
        Normalize GolfNow API response.
        Confirmed structure:
          ttResults.teeTimes[] = {
            facility: {...},
            time: "2026-03-20T07:00:00",   ← tee time
            teeTimeRates: [ { holeCount, playerRule, singlePlayerPrice, ... } ]
          }
        """
        slots = []
        tee_times = []
        if isinstance(data, dict):
            tee_times = (data.get("ttResults") or {}).get("teeTimes") or []

        for group in tee_times:
            facility = group.get("facility") or {}
            course_name = facility.get("name", "")

            # time is a dict: {"date": "2026-03-20T15:20:00+00:00", "formatted": "3:20", ...}
            raw_time = group.get("time") or {}
            if isinstance(raw_time, dict):
                time_str = raw_time.get("date") or ""
            else:
                time_str = str(raw_time)
            # Normalize to "YYYY-MM-DD HH:MM"
            if "T" in time_str:
                time_str = time_str.split("+")[0].split("Z")[0].replace("T", " ")[:16]

            rates = group.get("teeTimeRates") or []
            if not rates:
                # No rates but time exists — still show as a slot
                slots.append({
                    "time":            time_str,
                    "available_spots": 4,
                    "green_fee":       0,
                    "holes":           18,
                    "rate_type":       "",
                    "course_name":     course_name,
                    "_raw":            group,
                })
                continue

            # Use cheapest/first rate for price info
            rate = rates[0]
            fee = 0
            try:
                price_obj = rate.get("singlePlayerPrice") or {}
                due = price_obj.get("dueAtCourse") or price_obj.get("total") or {}
                fee = due.get("value") or 0
            except Exception:
                pass

            # playerRule tells us group sizes allowed e.g. "TwoFour"
            player_rule = rate.get("playerRule", "")
            spots = 4 if "Four" in player_rule else 2

            slots.append({
                "time":            time_str,
                "available_spots": spots,
                "green_fee":       fee,
                "holes":           rate.get("holeCount") or 18,
                "rate_type":       player_rule,
                "course_name":     course_name,
                "_raw":            group,
            })

        logger.info(f"GolfNow normalized {len(slots)} slots from {len(tee_times)} groups")
        return slots

    @staticmethod
    def booking_url(course_id: str, date: str, players: int = 2, platform: str = "teeitup") -> str:
        """Build a direct booking URL."""
        # date is MM-DD-YYYY, convert to YYYY-MM-DD for URL
        try:
            d = datetime.strptime(date, "%m-%d-%Y")
            url_date = d.strftime("%Y-%m-%d")
        except ValueError:
            url_date = date

        if platform == "teeitup":
            return f"https://book.teeitup.golf/tee-times?facilityId={course_id}&date={url_date}&players={players}"
        else:
            return f"https://www.golfnow.com/tee-times/facility/{course_id}#date={url_date}&players={players}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _time_to_minutes(t: str) -> int:
    h, m = map(int, t.strip().split(":"))
    return h * 60 + m


def _parse_slot_time(slot_time: str):
    """Parse various time formats into minutes since midnight."""
    if not slot_time:
        return None
    slot_time = str(slot_time).strip()

    # 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DDTHH:MM'
    for sep in (" ", "T"):
        if sep in slot_time:
            slot_time = slot_time.split(sep)[1]
            break

    m = re.match(r"^(\d{1,2}):(\d{2})", slot_time)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    # Unix epoch
    if re.match(r"^\d{10,}$", slot_time):
        try:
            dt = datetime.fromtimestamp(int(slot_time))
            return dt.hour * 60 + dt.minute
        except Exception:
            pass

    return None
