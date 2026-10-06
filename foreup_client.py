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
    def __init__(self, email: str = "", password: str = ""):
        # Credentials are optional: public booking classes serve availability
        # without a login. Only restricted (member/pass-holder) classes need one.
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
        if not self.email or not self.password:
            raise ValueError("Email and password are required. Configure them in Settings.")
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
        # ForeUp returns person_id/user_id and logged_in:True on success
        user_id = (data.get("person_id") or data.get("user_id") or
                   data.get("player_id") or data.get("customer_id") or
                   data.get("id") or data.get("jwt"))
        if not user_id and not data.get("logged_in"):
            raise ValueError(f"Login failed – unexpected ForeUp response: {data}")
        self._logged_in = True
        self._customer_id = user_id
        # Also store JWT for future requests if present
        if data.get("jwt"):
            self.session.headers["Authorization"] = f"Bearer {data['jwt']}"
        logger.info(f"Logged in as {self.email} (person_id={data.get('person_id')})")
        return data

    def _ensure_logged_in(self, course_id: str = "19536"):
        if self._logged_in:
            return
        if self.email and self.password:
            self.login(course_id)
        else:
            # Public mode — we still need a PHPSESSID or ForeUp answers
            # "Refresh required" to the availability call.
            self._init_session(course_id)

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

        all_times = _parse_times_response(resp, booking_class or course_id, schedule_id)

        # Log first slot so we can see the raw time format
        if all_times:
            logger.info(f"First slot sample: {all_times[0]}")

        # Filter by time window
        from_minutes = _time_to_minutes(time_from)
        to_minutes = _time_to_minutes(time_to)

        filtered = []
        for slot in all_times:
            raw_time = str(slot.get("time", ""))
            slot_minutes = _parse_slot_time(raw_time)
            if slot_minutes is None:
                logger.debug(f"Could not parse slot time: {raw_time!r}")
                continue
            if from_minutes <= slot_minutes <= to_minutes:
                filtered.append(slot)

        logger.info(
            f"Fetched {len(all_times)} times for schedule {schedule_id} on {date}, "
            f"{len(filtered)} in window {time_from}–{time_to} "
            f"(from={from_minutes}min, to={to_minutes}min)"
        )
        return filtered

    # ── Booking ───────────────────────────────────────────────────────────────

    def hold(self, slot: dict, players: int, holes: int = 18) -> dict:
        """
        Put a tee time on hold via ForeUp's pending_reservation endpoint.

        This is the same call the booking page makes when you click a time: it
        reserves the slot for a few minutes so you can complete checkout. It does
        NOT finalize a booking — the round is not confirmed and nothing is
        charged until a human completes checkout in the browser.

        `slot` is a raw slot dict straight from fetch_tee_times, which already
        carries every id the endpoint needs.

        Returns the parsed response on success. Raises on refusal — notably when
        the booking class has block_online_booking set, in which case the course
        does not permit online booking for that player category at all.
        """
        self._ensure_logged_in(str(slot.get("course_id") or self._course_id))

        payload = build_hold_payload(slot, players, holes)
        logger.info(f"Hold payload: {payload}")

        # ForeUp's own client form-encodes this (jQuery `data: params`). Sending
        # JSON gets a bare HTTP 500 with an empty body.
        resp = self.session.post(
            f"{BASE}/index.php/api/booking/pending_reservation",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            timeout=15,
        )

        if resp.status_code in (401, 403):
            raise PermissionError(
                f"ForeUp refused the hold (HTTP {resp.status_code}). This booking "
                f"class likely does not permit online booking. {resp.text[:200]}"
            )
        if not resp.ok:
            raise RuntimeError(f"Hold failed: HTTP {resp.status_code} – {resp.text[:300]}")

        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"Hold returned non-JSON: {resp.text[:200]!r}")

        if data is False or (isinstance(data, dict) and data.get("success") is False):
            msg = data.get("msg") if isinstance(data, dict) else "returned `false`"
            raise RuntimeError(f"ForeUp declined the hold: {msg}")

        logger.info(f"Hold placed on {slot.get('time')}: {str(data)[:200]}")
        return data if isinstance(data, dict) else {"response": data}

    def refresh_hold(self, reservation_id: str) -> bool:
        """
        Push a pending reservation's expiry back out.

        ForeUp holds a slot for HOLD_SECONDS; the booking page keeps yours alive
        while you are in checkout by POSTing here (no body). Doing the same lets
        a sniped time survive longer than five minutes, so you are not forced to
        confirm within seconds of the alert.
        """
        resp = self.session.post(
            f"{BASE}/index.php/api/booking/refresh_pending_reservation/{reservation_id}",
            timeout=15,
        )
        if not resp.ok:
            logger.warning(
                f"Hold refresh failed for {reservation_id}: HTTP {resp.status_code}")
            return False
        return True

    def release_hold(self, reservation_id: str) -> bool:
        """Give a held slot back, so it returns to the tee sheet immediately."""
        resp = self.session.delete(
            f"{BASE}/index.php/api/booking/pending_reservation/{reservation_id}",
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if resp.ok:
            logger.info(f"Released hold {reservation_id}")
            return True
        logger.warning(
            f"Could not release hold {reservation_id}: HTTP {resp.status_code} "
            f"— it will lapse on its own within {HOLD_SECONDS // 60} minutes.")
        return False

    def prewarm(self, course_id: str):
        """
        Establish the PHPSESSID ahead of a release window.

        Without this the first hold pays for a full booking-page fetch, which
        measured ~900ms of the 1.1s round trip — long enough to lose the race.
        """
        self._ensure_logged_in(str(course_id))

    @staticmethod
    def booking_url(course_id: str, date: str, players: int = 2) -> str:
        """
        Build a direct ForeUp booking URL for a specific date.
        date format: MM-DD-YYYY
        """
        return (
            f"{BASE}/index.php/booking/{course_id}"
            f"?date={date}&players={players}#/teetimes"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

# Exactly the fields ForeUp's online-booking bundle picks off the tee time when
# it creates a pending reservation:
#   params = _.pick(attributes, [...])   →   $.ajax({type:'POST', data: params})
# Sending anything else (or sending JSON) returns a bare HTTP 500.
HOLD_FIELDS = (
    "time", "holes", "players", "carts", "schedule_id", "teesheet_side_id",
    "course_id", "booking_class_id", "duration", "foreup_discount",
    "foreup_trade_discount_rate", "trade_min_players", "cart_fee",
    "cart_fee_tax", "green_fee", "green_fee_tax",
)

# How long ForeUp holds a pending reservation, from the booking bundle's
# PendingReservation.time_limit. Refreshable via refresh_pending_reservation.
HOLD_SECONDS = 300


def _jq(value):
    """
    Encode a value the way jQuery's $.param would, so the payload matches what
    ForeUp's server expects. Python would render booleans as "True"/"False",
    which PHP reads as non-empty (truthy) strings.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def build_hold_payload(slot: dict, players: int, holes: int = 18,
                       carts: int = 0, duration: int = 1) -> dict:
    """
    Build the pending_reservation body from a raw tee time slot.

    The slot straight from fetch_tee_times already carries every id and price
    ForeUp wants; players/holes/carts/duration come from the job.
    """
    source = dict(slot)
    source.update({
        "players":  players,
        "holes":    holes,
        "carts":    carts,
        "duration": duration,
    })
    return {
        field: _jq(source[field])
        for field in HOLD_FIELDS
        if source.get(field) is not None
    }


def _parse_times_response(resp: requests.Response, booking_class: str, schedule_id: str) -> list:
    """
    Turn a /api/booking/times response into a list of slots.

    ForeUp answers a bad booking_class/schedule_id pairing with a bare `false`
    (HTTP 200), which used to surface as an opaque "Poll error" every cycle.
    Name that case explicitly so it is obvious in the job log.
    """
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"ForeUp returned non-JSON: {resp.text[:200]!r}")

    if isinstance(data, list):
        return data

    if data is False:
        raise ValueError(
            f"ForeUp rejected booking_class={booking_class} / schedule_id={schedule_id} "
            f"(returned `false`). These IDs are wrong for this course — re-resolve it "
            f"so the correct booking class is detected."
        )

    if isinstance(data, dict):
        msg = data.get("msg") or data.get("message") or str(data)[:200]
        raise ValueError(f"ForeUp error: {msg}")

    raise ValueError(f"Unexpected tee times response: {data!r}")


def probe_booking_class(course_id: str, schedule_id: str, booking_class: str) -> bool:
    """
    Read-only check: is this booking class usable without a login?

    Used during course resolution to tell a public class from a restricted one.
    A restricted class answers 401; a wrong ID pairing answers `false`.

    Probes a couple of dates because a single date can legitimately come back
    empty (sheet not open yet, or the course bars that group size). Uses
    players=2, which virtually every course allows — players=1 is often blocked
    and would make a perfectly good class look dead.
    """
    from datetime import date, timedelta

    client = ForeUpClient()
    usable = False
    for days_ahead in (3, 5):
        try:
            times = client.fetch_tee_times(
                course_id=course_id,
                schedule_id=schedule_id,
                date=(date.today() + timedelta(days=days_ahead)).strftime("%m-%d-%Y"),
                time_from="00:00",
                time_to="23:59",
                players=2,
                booking_class=booking_class,
            )
        except (PermissionError, ValueError, RuntimeError):
            return False
        if times:
            return True
        # Empty but not rejected — the pairing is valid, sheet just isn't open.
        usable = True
    return usable


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
    Handles:
      'YYYY-MM-DD HH:MM'  ← confirmed live format e.g. '2026-03-20 15:20'
      'HH:MM' or 'HH:MM:SS'
      unix epoch int/str
    """
    if not slot_time:
        return None
    slot_time = str(slot_time).strip()

    # 'YYYY-MM-DD HH:MM' — extract the time portion after the space
    if " " in slot_time:
        slot_time = slot_time.split(" ")[1]

    # HH:MM or HH:MM:SS
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
