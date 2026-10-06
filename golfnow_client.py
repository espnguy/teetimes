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

import os
import re
import logging
import secrets
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
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin":          "https://www.golfnow.com",
    "Referer":         "https://www.golfnow.com/",
    "Sec-Ch-Ua":          '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile":   "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# Headers that identify the browser. curl_cffi sends its own set that matches
# the Chrome TLS fingerprint it impersonates; overriding them with a different
# Chrome version is exactly the mismatch Cloudflare looks for.
_BROWSER_ID_HEADERS = ("User-Agent", "Accept-Encoding",
                       "Sec-Ch-Ua", "Sec-Ch-Ua-Mobile", "Sec-Ch-Ua-Platform")

try:
    from curl_cffi import requests as _cffi_requests
except ImportError:  # local dev without curl_cffi installed
    _cffi_requests = None


def proxy_url() -> str:
    """
    The residential proxy for GolfNow/TeeItUp traffic, from the PROXY_URL env var.

    GolfNow's Cloudflare blocks cloud hosts (Railway) by address, so these
    requests have to leave from a home connection. Residential providers give
    one gateway URL and rotate the exit IP themselves. A literal "{session}" in
    the URL is replaced per session with a random id, for providers that pin
    ("sticky") an IP to a session id in the username — one session then keeps
    one IP, e.g. through a snipe burst, and the next poll gets a fresh one.
    """
    url = os.environ.get("PROXY_URL", "").strip()
    if "{session}" in url:
        url = url.replace("{session}", secrets.token_hex(4))
    return url


def describe_proxy(url: str) -> str:
    """host:port of a proxy URL, never its credentials — safe for logs."""
    p = urlparse(url)
    return f"{p.hostname}:{p.port}" if p.hostname else "proxy"


def new_session(use_proxy: bool = False):
    """
    An HTTP session for GolfNow / TeeItUp (and Purpose Golf).

    GolfNow sits behind Cloudflare, which 403s plain python-requests from cloud
    hosts. curl_cffi makes the TLS/HTTP2 handshake look like real Chrome.
    Falls back to requests (with our spoofed headers) if curl_cffi is missing.

    use_proxy routes it through PROXY_URL when that is set. The session's
    `.via` says which route it took, for the job log.
    """
    proxy = proxy_url() if use_proxy else ""
    proxies = {"http": proxy, "https": proxy} if proxy else None
    if _cffi_requests is not None:
        session = _cffi_requests.Session(impersonate="chrome", proxies=proxies)
        session.headers.update(
            {k: v for k, v in HEADERS.items() if k not in _BROWSER_ID_HEADERS})
    else:
        session = requests.Session()
        session.headers.update(HEADERS)
        if proxies:
            session.proxies.update(proxies)
    session.via = f"via proxy {describe_proxy(proxy)}" if proxy else "direct"
    return session


KENNA = "https://phx-api-be-east-1b.kenna.io"


def _kenna_headers(alias: str) -> dict:
    return request_headers(**{
        "Accept":     "application/json",
        "Origin":     f"https://{alias}.book.teeitup.golf",
        "Referer":    f"https://{alias}.book.teeitup.golf/",
        "X-Be-Alias": alias,
    })


def kenna_facilities(session, alias: str) -> list[dict]:
    """The facilities behind a TeeItUp booking site, or [] if the alias doesn't exist."""
    resp = session.get(f"{KENNA}/alias/{alias}/facilities",
                       headers=_kenna_headers(alias), timeout=15)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    return [f for f in data if isinstance(f, dict) and f.get("id")] if isinstance(data, list) else []


def find_teeitup_alias(facility_id: str, candidates: list[str]) -> str:
    """
    The TeeItUp alias whose booking site serves this GolfNow facility, or "".
    The alias usually equals the GolfNow URL slug ("pecan-hollow-golf-course");
    verify it, since a wrong alias errors rather than returning nothing.
    """
    session = new_session(use_proxy=True)
    for alias in dict.fromkeys(c for c in candidates if c):
        try:
            if any(str(f["id"]) == str(facility_id) for f in kenna_facilities(session, alias)):
                return alias
        except Exception as e:
            logger.warning(f"TeeItUp alias {alias} check failed: {e}")
    return ""


def request_headers(**extra) -> dict:
    """Per-request headers for new_session(), without clobbering its browser identity."""
    base = HEADERS if _cffi_requests is None else {
        k: v for k, v in HEADERS.items() if k not in _BROWSER_ID_HEADERS}
    return {**base, **extra}


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
        self.session = new_session(use_proxy=True)
        self._golfnow_session_ready = False

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

        be_alias = kwargs.get("be_alias", "")

        def fetch():
            if platform == "teeitup" or be_alias:
                # GolfNow courses with a TeeItUp site go this way too.
                return self._fetch_teeitup(course_id, api_date, players, holes, be_alias=be_alias)
            return self._fetch_golfnow(course_id, api_date, players, holes)

        try:
            all_times = fetch()
        except Exception as e:
            if "403" not in str(e):
                raise
            if not proxy_url():
                raise RuntimeError(
                    "GolfNow blocks this server's address (403). Set PROXY_URL in "
                    "Railway to a residential proxy so GolfNow/TeeItUp requests "
                    "leave from a home connection.")
            # A rotating proxy hands a new connection a new exit IP — one retry.
            logger.warning(f"403 {self.session.via}; retrying on a fresh exit IP")
            self.session = new_session(use_proxy=True)
            self._golfnow_session_ready = False
            try:
                all_times = fetch()
            except Exception as e2:
                if "403" in str(e2):
                    raise RuntimeError(
                        f"GolfNow blocked two exit IPs from the proxy (403, "
                        f"{self.session.via}). Check the proxy is residential, not "
                        f"datacenter, and that it is set to rotate.")
                raise

        # Filter by time window and player count
        from_min = _time_to_minutes(time_from)
        to_min   = _time_to_minutes(time_to)

        filtered = []
        for slot in all_times:
            slot_min = _parse_slot_time(slot.get("time", ""))
            if slot_min is None or not (from_min <= slot_min <= to_min):
                continue
            # TeeItUp says exactly which group sizes a time takes and how many are left
            if slot.get("allowed_players") and players not in slot["allowed_players"]:
                continue
            if "allowed_players" in slot and slot.get("available_spots", 4) < players:
                continue
            # Filter by player count — check if requested count is in allowed group sizes
            player_rule = slot.get("rate_type", "")  # e.g. "TwoFour", "Two", "TwoThreeFour"
            if player_rule:
                allowed = set()
                if "One" in player_rule:   allowed.add(1)
                if "Two" in player_rule:   allowed.add(2)
                if "Three" in player_rule: allowed.add(3)
                if "Four" in player_rule:  allowed.add(4)
                if allowed and players not in allowed:
                    continue
            filtered.append(slot)

        logger.info(
            f"GolfNow: fetched {len(all_times)} times for facility {course_id} {self.session.via} "
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
        Fetch from TeeItUp (Kenna), the booking engine GolfNow runs for courses.

        Confirmed from DevTools on pecan-hollow-golf-course.book.teeitup.golf:
          GET {KENNA}/alias/{alias}/facilities              → numeric ids + timezone
          GET {KENNA}/v2/tee-times?date=YYYY-MM-DD&facilityIds=1307
          Header X-Be-Alias: {alias} — required, and must belong to the facility.

        This also serves GolfNow courses: golfnow.com's own search API 403s
        requests from cloud hosts, this one doesn't. (The old /tee-time/locks
        endpoint lists times sitting in someone's cart, not open ones.)
        """
        alias = be_alias or facility_id
        facilities = kenna_facilities(self.session, alias)
        if not facilities:
            raise ValueError(f"TeeItUp has no booking site '{alias}' — re-detect the course.")
        ids = [str(f["id"]) for f in facilities]
        if str(facility_id) in ids:
            ids = [str(facility_id)]
        tz = facilities[0].get("timeZone") or "America/Chicago"

        resp = self.session.get(
            f"{KENNA}/v2/tee-times",
            params={"date": date, "facilityIds": ",".join(ids)},
            headers=_kenna_headers(alias),
            timeout=15,
        )
        resp.raise_for_status()
        slots = self._normalize_kenna(resp.json(), tz)
        logger.info(f"TeeItUp/Kenna: got {len(slots)} slots for {alias} ({','.join(ids)}) on {date}")
        return slots

    def _ensure_golfnow_session(self, facility_id: str):
        """GET the facility page first to obtain GolfNow session cookies."""
        if self._golfnow_session_ready:
            return
        try:
            self.session.get(
                f"https://www.golfnow.com/tee-times/facility/{facility_id}",
                headers={
                    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Fetch-Dest":  "document",
                    "Sec-Fetch-Mode":  "navigate",
                    "Sec-Fetch-Site":  "none",
                },
                timeout=15,
            )
            logger.info(f"GolfNow session initialized for facility {facility_id}")
        except Exception as e:
            logger.warning(f"GolfNow session init failed (continuing anyway): {e}")
        self._golfnow_session_ready = True

    def _fetch_golfnow(self, facility_id: str, date: str, players: int, holes: int) -> list[dict]:
        """
        Fetch from GolfNow.
        Confirmed endpoint + payload from DevTools:
          POST https://www.golfnow.com/api/tee-times/tee-time-search-results
          Body: JSON with facilityId, date (formatted "Mar 21 2026"), players, timeMin/timeMax, etc.
        timeMin/timeMax are in 30-min increments from midnight (10=5am, 42=9pm).
        """
        self._ensure_golfnow_session(facility_id)

        url = "https://www.golfnow.com/api/tee-times/tee-time-search-results"

        # Convert YYYY-MM-DD to "Mar 21 2026" format GolfNow expects
        from datetime import datetime as _dt
        try:
            d = _dt.strptime(date, "%Y-%m-%d")
            gn_date = f"{d.strftime('%b')} {d.day} {d.year}"  # e.g. "Mar 20 2026"
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

        headers = request_headers(**{
            "Accept":          "application/json, text/plain, */*",
            "Content-Type":    "application/json",
            "Origin":          "https://www.golfnow.com",
            "Referer":         f"https://www.golfnow.com/tee-times/facility/{facility_id}/search",
            "Sec-Fetch-Dest":  "empty",
            "Sec-Fetch-Mode":  "cors",
            "Sec-Fetch-Site":  "same-origin",
        })

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

    def _normalize_kenna(self, data, tz_name: str) -> list[dict]:
        """
        Normalize a Kenna v2/tee-times response:
          [{courseId, teetimes: [{teetime: "2026-10-09T21:37:00.000Z",
                                  maxPlayers, bookedPlayers,
                                  rates: [{allowedPlayers: [2, 4], holes, greenFeeCart}]}]}]
        Times are UTC; convert to the course's own clock like the other platforms.
        """
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
        slots = []
        for day in data if isinstance(data, list) else []:
            for item in day.get("teetimes") or []:
                try:
                    utc = datetime.fromisoformat(item["teetime"].replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    continue
                rates = item.get("rates") or []
                allowed = sorted({n for r in rates for n in (r.get("allowedPlayers") or [])})
                fees = [r.get("greenFeeCart") or r.get("greenFeeWalking") or 0 for r in rates]
                slots.append({
                    "time":            utc.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                    "available_spots": (item.get("maxPlayers") or 4) - (item.get("bookedPlayers") or 0),
                    "allowed_players": allowed,
                    "green_fee":       (min(f for f in fees if f) / 100) if any(fees) else 0,
                    "holes":           (rates[0].get("holes") if rates else None) or 18,
                    "rate_type":       "",
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

            # playerRule tells us max group size e.g. "TwoFour" = 2 or 4 players allowed
            # Use the max allowed as available_spots (conservative — actual may be less)
            player_rule = rate.get("playerRule", "")
            if "Four" in player_rule:
                spots = 4
            elif "Three" in player_rule:
                spots = 3
            elif "Two" in player_rule:
                spots = 2
            else:
                spots = 4  # default

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
