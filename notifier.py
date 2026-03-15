"""
Pushover push notification sender.

Pushover is a $5 one-time app purchase (iOS/Android), then free forever.
Sign up at https://pushover.net to get your User Key and create an App Token.

Set these two values in the dashboard Settings, or as environment variables:
  PUSHOVER_USER_TOKEN   — your personal user key
  PUSHOVER_APP_TOKEN    — the app/API token you create at pushover.net/apps
"""

import logging
import requests

logger = logging.getLogger(__name__)

PUSHOVER_API = "https://api.pushover.net/1/messages.json"


def send_pushover(
    user_token: str,
    app_token: str,
    title: str,
    message: str,
    url: str = "",
    url_title: str = "",
    priority: int = 1,   # 1 = high priority (bypasses quiet hours), 0 = normal
) -> bool:
    """
    Send a Pushover notification. Returns True on success, False on failure.

    Priority levels:
      -2  = lowest  (no sound/vibration)
       0  = normal
       1  = high    (bypasses quiet hours — good for tee time alerts)
       2  = emergency (requires acknowledgement — probably overkill)
    """
    if not user_token or not app_token:
        logger.warning("Pushover tokens not configured — skipping notification")
        return False

    payload = {
        "token":     app_token,
        "user":      user_token,
        "title":     title,
        "message":   message,
        "priority":  priority,
    }
    if url:
        payload["url"] = url
        payload["url_title"] = url_title or "Open Dashboard"

    try:
        resp = requests.post(PUSHOVER_API, data=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == 1:
            logger.info(f"Pushover notification sent: {title}")
            return True
        else:
            logger.error(f"Pushover error: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Pushover notification: {e}")
        return False


def notify_times_available(
    user_token: str,
    app_token: str,
    job: dict,
    times: list,
    dashboard_url: str = "",
) -> bool:
    """Send a tee time alert with a direct link to the ForeUp booking page."""
    date      = job.get("target_date", "?")
    time_from = job.get("time_from", "?")
    time_to   = job.get("time_to", "?")
    players   = job.get("players", "?")
    course_id = job.get("course_id", "")
    count     = len(times)

    # Format preview of available times — show all of them
    previews = []
    for t in times:
        raw = t.get("time", "")
        fee = t.get("green_fee")
        spots = t.get("available_spots", "")
        fee_str = f"  ${fee:.2f}" if fee is not None else ""
        spots_str = f"  {spots} open" if spots else ""
        previews.append(f"{_fmt_time(raw)}{fee_str}{spots_str}")
    preview_str = "\n".join(previews)

    # Format date nicely e.g. "Thu Mar 20"
    try:
        from datetime import datetime as dt
        d = dt.strptime(date, "%m-%d-%Y")
        pretty_date = d.strftime("%a %b %-d")
    except Exception:
        pretty_date = date

    title = f"⛳ {count} Tee Time{'s' if count > 1 else ''} — {pretty_date}"
    message = (
        f"{pretty_date} • {players} players\n"
        f"──────────────\n"
        f"{preview_str}\n"
        f"──────────────\n"
        f"ForeUp → select Public → {pretty_date}"
    )

    # Link goes straight to the booking page — platform-aware
    platform = job.get("platform", "foreup")
    if platform in ("teeitup", "golfnow"):
        from golfnow_client import GolfNowClient
        booking_url = GolfNowClient.booking_url(course_id, date, int(players), platform)
    else:
            booking_url = ForeUpClient.booking_url(course_id, date, int(players))

    return send_pushover(
        user_token=user_token,
        app_token=app_token,
        title=title,
        message=message,
        url=booking_url,
        url_title="Open ForeUp →",
        priority=1,
    )


def notify_test(user_token: str, app_token: str) -> bool:
    """Send a test notification to verify credentials work."""
    return send_pushover(
        user_token=user_token,
        app_token=app_token,
        title="⛳ Tee Time Booker",
        message="Test notification — your Pushover setup is working!",
        priority=0,
    )


def _fmt_time(raw) -> str:
    """Format a ForeUp time value into a readable string."""
    import re
    from datetime import datetime
    if not raw:
        return "Unknown"
    raw = str(raw).strip()
    if re.match(r"^\d{10,}$", raw):
        try:
            return datetime.fromtimestamp(int(raw)).strftime("%-I:%M %p")
        except Exception:
            pass
    m = re.match(r"^(\d{1,2}):(\d{2})", raw)
    if m:
        h, mn = int(m.group(1)), m.group(2)
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{mn} {ampm}"
    return raw
