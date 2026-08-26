"""
Exercise the hold lifecycle: win it, keep it alive, release it, and cap it.

The real thing runs once a week at 6am against a live tee sheet, so the logic
is verified here against a fake ForeUp instead.
"""
import logging, time
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.ERROR)
import db

STATE, LOGS = {}, []
db.load_all_jobs      = lambda: [dict(STATE)]
db.load_job           = lambda jid: dict(STATE) if jid == STATE["id"] else None
db.load_config        = lambda: {"pushover_user_token": "", "pushover_app_token": ""}
db.update_job_fields  = lambda jid, f: STATE.update(f)
db.append_job_log     = lambda jid, e: LOGS.append(e)
db.delete_job         = lambda jid: None

import scheduler as S
S.SNIPE_LEAD_SECONDS, S.SNIPE_BURST_INTERVAL, S.SNIPE_WINDOW_SECONDS = 2, 0.05, 5
S.TICK, S.HOLD_KEEPALIVE_INTERVAL, S.HOLD_MAX_MINUTES = 0.1, 0.3, 2 / 60  # 2s cap

calls = {"hold": 0, "refresh": 0, "release": 0, "prewarm": 0}

class FakeClient:
    def prewarm(self, course_id): calls["prewarm"] += 1
    def fetch_tee_times(self, **kw):
        return [{"time": "2026-08-30 07:12", "green_fee": 60.4, "available_spots": 4,
                 "course_id": 19536, "schedule_id": 1832, "teesheet_side_id": 1465,
                 "booking_class_id": 12800}]
    def hold(self, slot, players, holes=18):
        calls["hold"] += 1
        return {"success": True, "booked_reservation": True,
                "reservation_id": "TTID_TEST123"}
    def refresh_hold(self, rid):  calls["refresh"] += 1; return True
    def release_hold(self, rid):  calls["release"] += 1; return True

S.TeeTimeScheduler._client_for = staticmethod(lambda job, cfg: FakeClient())
S.notify_times_available = lambda **kw: True

def new_job():
    STATE.clear(); LOGS.clear()
    for k in list(calls): calls[k] = 0
    STATE.update({
        "id": "h1", "status": "polling", "platform": "foreup",
        "course_id": "19536", "schedule_id": "1832", "booking_class": "12800",
        "target_date": (datetime.now() + timedelta(days=10)).strftime("%m-%d-%Y"),
        "time_from": "07:00", "time_to": "12:00", "players": 2, "holes": 18,
        "snipe_at": datetime.now(timezone.utc).isoformat(),
        "notification_sent": False, "logs": [], "hold_result": None,
    })

def wait_for(pred, timeout=12):
    end = time.time() + timeout
    while time.time() < end:
        if pred(): return True
        time.sleep(0.05)
    return False

# ── 1. win the hold, keep it alive, then hit the cap ─────────────────────────
print("TEST 1  hold is won, refreshed, then released at the cap")
new_job()
sch = S.TeeTimeScheduler(); sch.start()
assert wait_for(lambda: (STATE.get("hold_result") or {}).get("reservation_id")), "no hold"
h = STATE["hold_result"]
print(f"   held {h['time']} reservation={h['reservation_id']} prewarm={calls['prewarm']}")
assert calls["prewarm"] == 1, "session should be pre-warmed before the burst"
assert h["released"] is False

assert wait_for(lambda: calls["refresh"] >= 2), f"expected refreshes, got {calls['refresh']}"
print(f"   keep-alive refreshed {calls['refresh']}x")

assert wait_for(lambda: (STATE.get("hold_result") or {}).get("released")), "cap did not release"
print(f"   released at cap (release calls={calls['release']})")
assert calls["release"] == 1
assert calls["hold"] == 1, f"should hold exactly once, got {calls['hold']}"
print(f"   exactly {calls['hold']} hold placed (no duplicates in the window)")
assert STATE.get("snipe_at") is None, "snipe should be disarmed after a hit"
sch.stop(); time.sleep(0.4)

# ── 2. manual release stops the keep-alive ───────────────────────────────────
print("\nTEST 2  manual release stops the keep-alive")
new_job()
S.HOLD_MAX_MINUTES = 5
sch = S.TeeTimeScheduler(); sch.start()
assert wait_for(lambda: (STATE.get("hold_result") or {}).get("reservation_id")), "no hold"
sch.release_hold("h1")
assert STATE["hold_result"]["released"] is True
assert STATE["status"] == "polling"
print(f"   released manually; status back to {STATE['status']}")
before = calls["refresh"]
time.sleep(1.2)
assert calls["refresh"] == before, "keep-alive kept running after release"
print(f"   keep-alive stopped (refreshes frozen at {before})")
sch.stop(); time.sleep(0.4)

print("\nPASS")
