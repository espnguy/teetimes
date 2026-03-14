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
    """Send a formatted tee time alert for a specific job."""
    date      = job.get("target_date", "?")
    time_from = job.get("time_from", "?")
    time_to   = job.get("time_to", "?")
    players   = job.get("players", "?")
    count     = len(times)

    # Format a preview of the first few available times
    previews = []
    for t in times[:3]:
        raw = t.get("time", "")
        fee = t.get("green_fee")
        fee_str = f" · ${fee}" if fee is not None else ""
        previews.append(f"  • {_fmt_time(raw)}{fee_str}")
    preview_str = "\n".join(previews)
    if count > 3:
        preview_str += f"\n  …and {count - 3} more"

    # If only one time passed, it's a booking confirmation
    if count == 1:
        title   = f"✅ Tee Time Booked!"
        message = (
            f"{date}  ({players} players)\n\n"
            f"{preview_str}\n\n"
            f"Your tee time has been reserved."
        )
    else:
        title   = f"⛳ {count} Tee Time{'s' if count > 1 else ''} Available — Action Required!"
        message = (
            f"{date}  {time_from}–{time_to}  ({players} players)\n\n"
            f"{preview_str}\n\n"
            f"Auto-booking failed. Open dashboard to confirm manually."
        )

    return send_pushover(
        user_token=user_token,
        app_token=app_token,
        title=title,
        message=message,
        url=dashboard_url or "",
        url_title="Confirm & Book →",
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
