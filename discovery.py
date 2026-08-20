"""
Course discovery — "what can I actually watch near me?"

Given a US zip code, finds nearby golf courses and works out which booking
platform each one uses, so the dashboard can say up front whether a course is
supported before you try to add a job for it.

Sources, in order:
  1. zippopotam.us  — zip → lat/lon (free, no key)
  2. OpenStreetMap Overpass — golf courses within a radius, with websites
  3. course_resolver.sniff_platform — fetch each website, identify the platform

Step 3 is the slow part, so websites are sniffed concurrently and the verdict is
cached in Postgres keyed by website URL.
"""

import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from course_resolver import sniff_platform

logger = logging.getLogger(__name__)

ZIP_API = "https://api.zippopotam.us/us/{zip}"

# Overpass rejects the default python-requests agent with a 406, and instances
# go down often enough to be worth a fallback.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
OVERPASS_HEADERS = {
    "User-Agent": "teetimes-watcher/1.0 (personal tee time watcher)",
    "Accept": "application/json",
}

# Platforms this app can actually poll today.
SUPPORTED = {"foreup", "golfnow", "teeitup"}

# Recognised but unsupported — naming them beats a bare "unknown".
KNOWN_UNSUPPORTED = {
    "chronogolf": "Chronogolf / Lightspeed",
    "teesnap":    "Teesnap",
    "quick18":    "Quick18",
}

DEFAULT_RADIUS_MILES = 25
MAX_SNIFF_WORKERS    = 8


def geocode_zip(zip_code: str) -> dict | None:
    """Turn a 5-digit US zip into {lat, lon, city, state}."""
    zip_code = (zip_code or "").strip()[:5]
    if not zip_code.isdigit() or len(zip_code) != 5:
        return None
    try:
        resp = requests.get(ZIP_API.format(zip=zip_code), timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        place = (resp.json().get("places") or [None])[0]
        if not place:
            return None
        return {
            "lat":   float(place["latitude"]),
            "lon":   float(place["longitude"]),
            "city":  place.get("place name", ""),
            "state": place.get("state abbreviation", ""),
            "zip":   zip_code,
        }
    except Exception as e:
        logger.warning(f"Could not geocode {zip_code}: {e}")
        return None


def find_nearby_courses(lat: float, lon: float, radius_miles: int) -> list[dict]:
    """Golf courses within radius_miles of a point, via OpenStreetMap."""
    radius_m = int(radius_miles * 1609.34)
    query = f"""
        [out:json][timeout:45];
        (
          way["leisure"="golf_course"](around:{radius_m},{lat},{lon});
          relation["leisure"="golf_course"](around:{radius_m},{lat},{lon});
        );
        out tags center;
    """
    elements = None
    for mirror in OVERPASS_MIRRORS:
        try:
            resp = requests.post(mirror, data={"data": query},
                                 headers=OVERPASS_HEADERS, timeout=60)
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            break
        except Exception as e:
            logger.warning(f"Overpass mirror {mirror} failed: {e}")
    if elements is None:
        logger.error("All Overpass mirrors failed")
        return []

    courses = []
    for el in elements:
        tags = el.get("tags") or {}
        name = tags.get("name")
        # Unnamed polygons and driving ranges are noise.
        if not name or tags.get("golf") == "driving_range":
            continue
        website = (tags.get("website") or tags.get("contact:website") or "").strip()
        if website and not website.startswith("http"):
            website = f"https://{website}"
        courses.append({
            "name":    name,
            "website": website,
            "lat":     el.get("center", {}).get("lat") or el.get("lat"),
            "lon":     el.get("center", {}).get("lon") or el.get("lon"),
            "private": _looks_private(name, tags),
        })

    # De-duplicate courses mapped as both a way and a relation.
    seen, unique = set(), []
    for c in courses:
        key = c["name"].strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _looks_private(name: str, tags: dict) -> bool:
    """
    Private clubs cannot be booked by the public, so they are noise in a list of
    watchable courses. OSM tags access=private inconsistently, so fall back to
    the naming convention.
    """
    if tags.get("access") in ("private", "members"):
        return True
    low = name.lower()
    return any(w in low for w in ("country club", "cc ", " cc", "private"))


def _match_saved(name: str, saved: dict) -> dict | None:
    """
    Cross-reference a discovered name against courses already resolved in this
    app. OSM names are often merged or abbreviated ("Cowboys Golf Club and
    Grapevine Golf Course"), so match on containment in either direction.
    """
    low = name.lower().strip()
    for course in saved.values():
        stored = (course.get("name") or "").lower().strip()
        if not stored or len(stored) < 5:
            continue
        if stored in low or low in stored:
            return course
    return None


def _verdict(platform: str) -> dict:
    if platform in SUPPORTED:
        return {"supported": True, "platform_label": platform, "note": ""}
    if platform in KNOWN_UNSUPPORTED:
        return {
            "supported": False,
            "platform_label": KNOWN_UNSUPPORTED[platform],
            "note": f"{KNOWN_UNSUPPORTED[platform]} is not supported yet.",
        }
    return {
        "supported": False,
        "platform_label": "unknown",
        "note": "No booking platform detected — paste the booking URL directly to try anyway.",
    }


def discover(zip_code: str, radius_miles: int = DEFAULT_RADIUS_MILES,
             use_cache: bool = True) -> dict:
    """
    Full discovery for a zip code.

    Returns {location, courses: [...], counts}. Each course carries its detected
    platform and whether this app can poll it.
    """
    location = geocode_zip(zip_code)
    if not location:
        return {"error": f"'{zip_code}' is not a valid US zip code."}

    courses = find_nearby_courses(location["lat"], location["lon"], radius_miles)
    if not courses:
        return {"location": location, "courses": [], "counts": {"total": 0, "supported": 0}}

    cache = db.load_platform_cache() if use_cache else {}
    saved = db.load_courses()

    def resolve(course: dict) -> dict:
        # A course already resolved in this app is authoritative — it beats
        # anything sniffed, and covers courses OSM has no website for.
        known = _match_saved(course["name"], saved)
        if known:
            platform = known.get("platform") or "foreup"
            return {**course,
                    "name":        known.get("name") or course["name"],
                    "platform":    platform,
                    "booking_url": known.get("url") or "",
                    "already_saved": True,
                    **_verdict(platform)}

        site = course["website"]
        if not site:
            return {**course, "platform": "unknown", "booking_url": "",
                    **_verdict("unknown")}
        if site in cache:
            hit = cache[site]
            return {**course, "platform": hit["platform"],
                    "booking_url": hit["booking_url"], **_verdict(hit["platform"])}
        found = sniff_platform(site)
        db.save_platform_cache(site, found["platform"], found["booking_url"])
        return {**course, **found, **_verdict(found["platform"])}

    resolved = []
    with ThreadPoolExecutor(max_workers=MAX_SNIFF_WORKERS) as pool:
        futures = {pool.submit(resolve, c): c for c in courses}
        for fut in as_completed(futures):
            try:
                resolved.append(fut.result())
            except Exception as e:
                course = futures[fut]
                logger.warning(f"Sniff failed for {course['name']}: {e}")
                resolved.append({**course, "platform": "unknown",
                                 "booking_url": "", **_verdict("unknown")})

    # Watchable public courses first, then recognised-but-unsupported, with
    # private clubs last — they cannot be booked online at all.
    resolved.sort(key=lambda c: (
        c.get("private", False),
        not c["supported"],
        c["platform"] == "unknown",
        c["name"].lower(),
    ))

    return {
        "location": location,
        "radius_miles": radius_miles,
        "courses": resolved,
        "counts": {
            "total":     len(resolved),
            "supported": sum(1 for c in resolved if c["supported"]),
            "private":   sum(1 for c in resolved if c.get("private")),
        },
    }
