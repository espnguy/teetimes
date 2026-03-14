"""
ForeUp HTTP client.

Endpoints confirmed via browser DevTools on foreupsoftware.com/index.php/booking/19536:
  POST /index.php/api/booking/users/login       → authenticate
  GET  /index.php/api/booking/times             → available tee times
  POST /index.php/api/booking/pending_reservation → reserve a tee time
"""

import re
import logging
import requests
from urllib.parse import urlparse, parse_qs
from datetime import datetime

logger = logging.getLogger(__name__)

BASE = "https://foreupsoftware.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    ),
    "Accept":            "application/json, text/javascript, */*; q=0.01",
    "Accept-Language":   "en-US,en;q=0.9",
    "X-Requested-With":  "XMLHttpRequest",
    "X-Fu-Golfer-Location": "foreup",
    "Api-Key":           "no_limits",
    "Origin":            "https://foreupsoftware.com",
    "Referer":           "https://foreupsoftware.com/index.php/booking/19536",
    "Sec-Fetch-Dest":    "empty",
    "Sec-Fetch-Mode":    "cors",
    "Sec-Fetch-Site":    "same-origin",
}


def parse_course_url(url: str) -> dict:
    """
    Extract course_id, schedule_id, and booking_class from a ForeUp booking URL.

    Supported formats:
      https://foreupsoftware.com/index.php/booking/19536
      https://foreupsoftware.com/index.php/booking/19536/1832
      https://foreupsoftware.com/index.php/api/booking/times?schedule_id=1832&booking_class=12800&...
    """
    # Pull query params before stripping them
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    clean_url = url.split("#")[0].rstrip("/").split("?")[0]
    parts = clean_url.split("/")

    # Try to find booking segment
    course_id = None
    schedule_id = None
    for key in ("booking",):
        if key in parts:
            idx = parts.index(key)
            try:
                candidate = parts[idx + 1]
                if candidate.isdigit():
                    course_id = candidate
                    next_seg = parts[idx + 2] if len(parts) > idx + 2 else ""
                    if next_seg.isdigit():
                        schedule_id = next_seg
            except IndexError:
                pass

    # Fall back to query params (e.g. pasted from DevTools)
    if not course_id:
        course_id = (qs.get("booking_class", [None])[0] or
                     qs.get("course_id", [None])[0])

    if not course_id:
        raise ValueError(
            "Could not extract course ID from URL. "
            "Please paste the booking page URL, e.g. "
            "https://foreupsoftware.com/index.php/booking/19536"
        )

    # schedule_id from path, or query param
    if not schedule_id:
        schedule_id = (qs.get("schedule_id", [None])[0] or course_id)

    # booking_class from query params or fall back to course_id
    booking_class = qs.get("booking_class", [None])[0] or ""

    return {
        "course_id": course_id,
        "schedule_id": schedule_id,
        "booking_class": booking_class,
        "booking_base": f"{BASE}/index.php/booking/{course_id}",
    }


class ForeUpClient:
    def __init__(self, email: str, password: str):
        if not email or not password:
            raise ValueError("Email and password are required. Configure them in Settings.")
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._logged_in = False
        self._customer_id = None
        self._course_id = "19536"
        self._booking_class_id = ""

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _init_session(self, course_id: str = "19536"):
        """
        Visit the booking page first to obtain a PHPSESSID cookie.
        ForeUp returns 'Refresh required' if no session cookie is present.
        """
        self._course_id = course_id
        booking_url = f"{BASE}/index.php/booking/{course_id}"
        try:
            resp = self.session.get(booking_url, timeout=15)
            # Try to extract booking_class_id from the page HTML
            import re
            m = re.search(r"booking_class_id[\"\s:=]+[\"']?(\d+)", resp.text)
            if m:
                self._booking_class_id = m.group(1)
            logger.info(f"Session initialized from {booking_url} (booking_class_id={self._booking_class_id})")
        except Exception as e:
            logger.warning(f"Could not init session: {e}")

    def login(self, course_id: str = "19536"):
        # Step 1 — get a session cookie by visiting the booking page
        self._init_session(course_id)

        # Step 2 — log in with that session active
        url = f"{BASE}/index.php/api/booking/users/login"
        payload = {
            "username":        self.email,
            "password":        self.password,
            "api_key":         "no_limits",
            "booking_class_id": self._booking_class_id,
            "course_id":       self._course_id,
        }
        resp = self.session.post(
            url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            timeout=15,
        )
        _check_response(resp, "Login")
        data = resp.json()
        # Accept any of the known ID fields
        user_id = (data.get("player_id") or data.get("customer_id")
                   or data.get("id") or data.get("token"))
        if not user_id:
            raise ValueError(f"Login failed – unexpected ForeUp response: {data}")
        self._logged_in = True
        self._customer_id = user_id
        logger.info(f"Logged in as {self.email} (id={user_id})")
        return data

    def _ensure_logged_in(self, course_id: str = "19536"):
        if not self._logged_in:
            self.login(course_id)

    # ── Tee Time Availability ─────────────────────────────────────────────────

    def fetch_tee_times(
        self,
        course_id: str,
        schedule_id: str,
        date: str,           # "MM-DD-YYYY"
        time_from: str,      # "HH:MM"
        time_to: str,        # "HH:MM"
        players: int = 2,
        holes: int = 18,
        booking_class: str = "",
    ) -> list[dict]:
        self._ensure_logged_in(course_id)

        url = f"{BASE}/index.php/api/booking/times"
        params = {
            "time": "all",
            "date": date,
            "holes": str(holes),
            "players": str(players),
            "booking_class": booking_class or course_id,
            "schedule_id": schedule_id,
            "schedule_ids[]": schedule_id,
            "specials_only": "0",
        }

        resp = self.session.get(url, params=params, timeout=15)
        _check_response(resp, "Fetch tee times")

        all_times = resp.json()
        if not isinstance(all_times, list):
            raise ValueError(f"Unexpected tee times response: {all_times}")

        # Filter by time window
        from_minutes = _time_to_minutes(time_from)
        to_minutes = _time_to_minutes(time_to)

        filtered = []
        for slot in all_times:
            slot_minutes = _parse_slot_time(str(slot.get("time", "")))
            if slot_minutes is None:
                continue
            if from_minutes <= slot_minutes <= to_minutes:
                filtered.append(slot)

        logger.info(
            f"Fetched {len(all_times)} times for schedule {schedule_id} on {date}, "
            f"{len(filtered)} in window {time_from}–{time_to}"
        )
        return filtered

    # ── Booking ───────────────────────────────────────────────────────────────

    def book_tee_time(
        self,
        course_id: str,
        schedule_id: str,
        time_data: dict,
        players: int = 2,
        booking_class: str = "",
    ) -> dict:
        self._ensure_logged_in(course_id)

        url = f"{BASE}/index.php/api/booking/pending_reservation"

        payload = {
            **time_data,
            "players": players,
            "carts": 0,
            "booking_class": booking_class or course_id,
            "schedule_id": schedule_id,
        }

        resp = self.session.post(url, json=payload, timeout=20)
        _check_response(resp, "Book tee time")

        result = resp.json()
        logger.info(f"Booking result: {result}")
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_response(resp: requests.Response, label: str):
    if resp.status_code == 401:
        raise PermissionError(f"{label}: Not authenticated (401). Check your credentials.")
    if resp.status_code == 403:
        raise PermissionError(f"{label}: Access denied (403).")
    if not resp.ok:
        raise RuntimeError(f"{label} failed: HTTP {resp.status_code} – {resp.text[:300]}")


def _time_to_minutes(t: str) -> int:
    h, m = map(int, t.strip().split(":"))
    return h * 60 + m


def _parse_slot_time(slot_time: str):
    """
    Parse ForeUp time field into minutes since midnight.
    Handles: 'HH:MM:SS', 'H:MMam', unix epoch int/str
    """
    if not slot_time:
        return None
    slot_time = str(slot_time).strip()

    m = re.match(r"^(\d{1,2}):(\d{2})", slot_time)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    if re.match(r"^\d{10,}$", slot_time):
        try:
            dt = datetime.fromtimestamp(int(slot_time))
            return dt.hour * 60 + dt.minute
        except Exception:
            pass

    return None
