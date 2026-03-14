"""
ForeUp HTTP client.

ForeUp doesn't publish a public API, but the booking UI makes standard
JSON calls that we can replicate with requests + a session cookie.

Endpoints discovered via browser DevTools on foreupsoftware.com:
  GET  /api/booking/19536/times        → available tee times
  POST /api/booking/19536/reserve      → reserve a tee time
  POST /index.php/user/login           → authenticate
"""

import re
import json
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
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def parse_course_url(url: str) -> dict:
    """
    Extract course_id and schedule_id from a ForeUp booking URL.

    Supported formats:
      https://foreupsoftware.com/index.php/booking/19536
      https://foreupsoftware.com/index.php/booking/19536/21234
      ...#/teetimes
    """
    # Strip fragment
    url = url.split("#")[0].rstrip("/")
    parts = url.split("/")

    # Find 'booking' index
    try:
        idx = parts.index("booking")
    except ValueError:
        raise ValueError("URL does not appear to be a ForeUp booking URL (no '/booking/' segment found)")

    try:
        course_id = parts[idx + 1]
    except IndexError:
        raise ValueError("Could not extract course_id from URL")

    # schedule_id may be the next segment
    try:
        schedule_id = parts[idx + 2] if len(parts) > idx + 2 else course_id
    except IndexError:
        schedule_id = course_id

    # Validate they look like numeric IDs
    if not course_id.isdigit():
        raise ValueError(f"Unexpected course_id format: {course_id}")

    return {
        "course_id": course_id,
        "schedule_id": schedule_id,
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

    # ── Auth ─────────────────────────────────────────────────────────────────

    def login(self):
        """Authenticate and store the session cookie."""
        url = f"{BASE}/index.php/user/login"
        payload = {
            "email": self.email,
            "password": self.password,
            "api_key": "no_limits",  # public key used by the ForeUp booking widget
        }
        resp = self.session.post(url, json=payload, timeout=15)
        _check_response(resp, "Login")
        data = resp.json()
        if not data.get("player_id") and not data.get("token"):
            raise ValueError(f"Login failed – ForeUp response: {data}")
        self._logged_in = True
        logger.info(f"Logged in as {self.email} (player_id={data.get('player_id')})")
        return data

    def _ensure_logged_in(self):
        if not self._logged_in:
            self.login()

    # ── Tee Time Availability ─────────────────────────────────────────────────

    def fetch_tee_times(
        self,
        course_id: str,
        schedule_id: str,
        date: str,           # "MM-DD-YYYY"
        time_from: str,      # "07:00"
        time_to: str,        # "10:00"
        players: int = 2,
        holes: int = 18,
    ) -> list[dict]:
        """
        Returns a filtered list of available tee time dicts.
        Each dict contains: time, available_spots, green_fee, holes, time_id, etc.
        """
        self._ensure_logged_in()

        url = f"{BASE}/api/booking/{course_id}/times"
        params = {
            "date": date,              # MM-DD-YYYY
            "time": "all",
            "holes": str(holes),
            "players": str(players),
            "schedule_id": schedule_id,
            "schedule": schedule_id,
            "api_key": "no_limits",
        }

        resp = self.session.get(url, params=params, timeout=15)
        _check_response(resp, "Fetch tee times")

        all_times = resp.json()
        if not isinstance(all_times, list):
            raise ValueError(f"Unexpected API response: {all_times}")

        # Filter by time window
        from_minutes = _time_to_minutes(time_from)
        to_minutes = _time_to_minutes(time_to)

        filtered = []
        for slot in all_times:
            slot_time = slot.get("time", "")
            slot_minutes = _parse_slot_time(slot_time)
            if slot_minutes is None:
                continue
            if from_minutes <= slot_minutes <= to_minutes:
                filtered.append(slot)

        logger.info(
            f"Fetched {len(all_times)} times for course {course_id} on {date}, "
            f"{len(filtered)} match window {time_from}–{time_to}"
        )
        return filtered

    # ── Booking ───────────────────────────────────────────────────────────────

    def book_tee_time(
        self,
        course_id: str,
        schedule_id: str,
        time_data: dict,
        players: int = 2,
    ) -> dict:
        """
        POSTs a reservation for the given time slot.
        time_data should be one of the dicts returned by fetch_tee_times().
        """
        self._ensure_logged_in()

        url = f"{BASE}/api/booking/{course_id}/reserve"

        # Build the reservation payload from the slot data
        payload = {
            **time_data,
            "players": players,
            "carts": 0,
            "schedule_id": schedule_id,
            "api_key": "no_limits",
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
    """Convert 'HH:MM' to total minutes since midnight."""
    h, m = map(int, t.strip().split(":"))
    return h * 60 + m


def _parse_slot_time(slot_time: str):
    """
    Parse ForeUp's time field (can be '08:00:00', '8:00am', or epoch int/str)
    into minutes since midnight.
    """
    if not slot_time:
        return None
    slot_time = str(slot_time).strip()

    # HH:MM or HH:MM:SS
    m = re.match(r"^(\d{1,2}):(\d{2})", slot_time)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    # Unix epoch (seconds)
    if slot_time.isdigit():
        try:
            dt = datetime.fromtimestamp(int(slot_time))
            return dt.hour * 60 + dt.minute
        except Exception:
            pass

    return None
