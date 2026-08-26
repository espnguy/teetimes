"""Exercise the snipe engine end to end with a fake DB and a fake course."""
import sys, types, time, logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.WARNING)

# ---- stub psycopg2 so db.py imports, then replace db's functions entirely ----
import db

STATE = {}
LOGS = []

def load_all_jobs(): return [dict(STATE)]
def load_job(jid):   return dict(STATE) if jid == STATE["id"] else None
def load_config():   return {"pushover_user_token": "", "pushover_app_token": ""}
def update_job_fields(jid, fields): STATE.update(fields)
def append_job_log(jid, entry): LOGS.append(entry)
def delete_job(jid): pass

db.load_all_jobs = load_all_jobs
db.load_job = load_job
db.load_config = load_config
db.update_job_fields = update_job_fields
db.append_job_log = append_job_log
db.delete_job = delete_job

import scheduler as S
S.SNIPE_LEAD_SECONDS = 3
S.SNIPE_BURST_INTERVAL = 0.05
S.SNIPE_WINDOW_SECONDS = 6
S.TICK = 0.1

OPEN_AT = time.time() + 5.0     # sheet "opens" 5s from now
attempts = {"n": 0}

class FakeClient:
    def fetch_tee_times(self, **kw):
        attempts["n"] += 1
        if time.time() < OPEN_AT:
            return []
        return [{"time": "2026-08-30 07:12", "green_fee": 60.4, "available_spots": 4,
                 "course_id": 19536, "schedule_id": 1832, "teesheet_id": 1832,
                 "booking_class_id": 12800, "teesheet_side_id": 1465}]
    def prewarm(self, course_id): pass
    def hold(self, slot, players, holes=18):
        return {"success": True, "reservation_id": "TTID_SNIPE"}
    def release_hold(self, rid): return True

S.TeeTimeScheduler._client_for = staticmethod(lambda job, cfg: FakeClient())
S.notify_times_available = lambda **kw: (
    LOGS.append(f"NOTIFY urgent={kw.get('urgent')} n={len(kw['times'])}") or True)

STATE.update({
    "id": "test1", "status": "polling", "platform": "foreup",
    "course_id": "19536", "schedule_id": "1832", "booking_class": "12800",
    "target_date": (datetime.now() + timedelta(days=10)).strftime("%m-%d-%Y"),
    "time_from": "07:00", "time_to": "12:00", "players": 2, "holes": 18,
    "snipe_at": datetime.fromtimestamp(OPEN_AT, tz=timezone.utc).isoformat(),
    "notification_sent": False, "logs": [],
})

sch = S.TeeTimeScheduler()
print(f"Release in 5.0s. lead={S.SNIPE_LEAD_SECONDS}s burst={S.SNIPE_BURST_INTERVAL}s\n")
t0 = time.time()
sch.start()

deadline = time.time() + 14
while time.time() < deadline:
    if STATE.get("status") == "available":
        break
    time.sleep(0.1)
sch.stop(); time.sleep(0.3)

detect = None
for l in LOGS:
    if "Sheet opened" in l: detect = l

print("--- job log ---")
for l in LOGS: print("  ", l)
print("\n--- results ---")
print("status      :", STATE.get("status"))
print("attempts    :", attempts["n"])
print("times found :", len(STATE.get("available_times") or []))

lag = None
for l in LOGS:
    if "Sheet opened" in l:
        lag = time.time() - OPEN_AT
print("detect lag  : ~%.2fs after release" % (lag if lag else -1))
assert STATE["status"] == "available", "should have flipped to available"
assert (STATE.get("hold_result") or {}).get("reservation_id") == "TTID_SNIPE",     "a snipe hit should park the slot"
assert any("NOTIFY urgent=True" in l for l in LOGS), "should notify at emergency priority"
assert STATE.get("snipe_at") is None, "snipe should disarm after a hit"
assert attempts["n"] > 20, f"should have burst-polled many times, got {attempts['n']}"
print("\nPASS")
