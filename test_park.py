"""
Parking: claim a found slot so nobody else takes it, then hand it back on
request. The hold is never refreshed, so it lapses on ForeUp's own 5 minute
timer if the user never responds.
"""
import logging, time
from datetime import datetime, timezone, timedelta
logging.basicConfig(level=logging.ERROR)
import db

STATE, LOGS = {}, []
db.load_all_jobs     = lambda: [dict(STATE)]
db.load_job          = lambda jid: dict(STATE) if jid == STATE["id"] else None
db.load_config       = lambda: {"pushover_user_token": "", "pushover_app_token": ""}
db.update_job_fields = lambda jid, f: STATE.update(f)
db.append_job_log    = lambda jid, e: LOGS.append(e)
db.delete_job        = lambda jid: None

import scheduler as S
S.SNIPE_LEAD_SECONDS, S.SNIPE_BURST_INTERVAL, S.SNIPE_WINDOW_SECONDS = 2, 0.05, 5
S.TICK = 0.1

calls = {"hold": 0, "release": 0, "refresh": 0}
notified = []

class FakeClient:
    def prewarm(self, course_id): pass
    def fetch_tee_times(self, **kw):
        return [{"time": "2026-08-30 07:12", "green_fee": 60.4, "available_spots": 4,
                 "course_id": 19536, "schedule_id": 1832, "teesheet_side_id": 1465,
                 "booking_class_id": 12800}]
    def hold(self, slot, players, holes=18):
        calls["hold"] += 1
        return {"success": True, "reservation_id": "TTID_PARK1"}
    def refresh_hold(self, rid): calls["refresh"] += 1; return True
    def release_hold(self, rid): calls["release"] += 1; return True

S.TeeTimeScheduler._client_for = staticmethod(lambda job, cfg: FakeClient())
S.notify_times_available = lambda **kw: (notified.append(kw.get("parked")) or True)

STATE.update({
    "id": "p1", "status": "polling", "platform": "foreup",
    "course_id": "19536", "schedule_id": "1832", "booking_class": "12800",
    "target_date": (datetime.now() + timedelta(days=4)).strftime("%m-%d-%Y"),
    "time_from": "07:00", "time_to": "12:00", "players": 2, "holes": 18,
    "snipe_at": datetime.now(timezone.utc).isoformat(),
    "notification_sent": False, "logs": [], "hold_result": None,
})

sch = S.TeeTimeScheduler(); sch.start()
end = time.time() + 12
while time.time() < end and not (STATE.get("hold_result") or {}).get("reservation_id"):
    time.sleep(0.05)

h = STATE.get("hold_result") or {}
assert h.get("reservation_id") == "TTID_PARK1", f"not parked: {h}"
print(f"parked {h['time']} -> {h['reservation_id']}")
assert calls["hold"] == 1, f"should park exactly once, got {calls['hold']}"
assert h["released"] is False
assert notified and notified[0] and notified[0]["reservation_id"] == "TTID_PARK1", \
    "notification must say it was parked"
print("notified with parked payload")

# The hold must NOT be kept alive - it has to lapse on ForeUp's own timer.
time.sleep(1.5)
assert calls["refresh"] == 0, f"parked holds must never be refreshed, got {calls['refresh']}"
print("no keep-alive: hold will lapse on ForeUp's 5 min timer")

# Expiry is ForeUp's limit, not ours.
span = (datetime.fromisoformat(h["expires_at"]) - datetime.fromisoformat(h["held_at"])).total_seconds()
assert span == S.HOLD_SECONDS == 300, f"expiry should be ForeUp's {S.HOLD_SECONDS}s, got {span}"
print(f"expiry window = {span:.0f}s (ForeUp's own limit)")

# Release hands it back.
out = sch.release_hold("p1")
assert out["released"] is True and calls["release"] == 1
print("released on request")

# Releasing twice must not double-call ForeUp.
sch.release_hold("p1")
assert calls["release"] == 1, "second release should be a no-op"
print("second release is a no-op")

sch.stop(); time.sleep(0.3)
print("\nPASS")
