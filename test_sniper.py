"""
The generated in-browser sniper is a string, so guard the parts that silently
break: the config must be substituted, and the hold body must carry exactly the
16 fields ForeUp requires (a partial payload returns a bare HTTP 500).
"""
import json, re
from sniper import sniper_js
import foreup_client as fc

JOB = {"course_id": "19536", "schedule_id": "1832", "booking_class": "12800",
       "target_date": "08-30-2026", "time_from": "07:00", "time_to": "10:00",
       "players": 4, "holes": 18}

js = sniper_js(JOB, "2026-08-24T11:00:00+00:00")

assert "__CONFIG__" not in js, "config placeholder was not substituted"
cfg = json.loads(re.search(r"const CFG = (\{.*?\});", js, re.S).group(1))
assert cfg["bookingClass"] == "12800" and cfg["scheduleId"] == "1832"
assert cfg["players"] == 4 and cfg["date"] == "08-30-2026"
assert cfg["releaseUtc"] == "2026-08-24T11:00:00+00:00"
print("config baked in:", {k: cfg[k] for k in ("date","timeFrom","timeTo","players")})

# The field list in the script must match the Python client exactly.
listed = re.search(r"const HOLD_FIELDS = \[(.*?)\];", js, re.S).group(1)
fields = tuple(re.findall(r"'([a-z_]+)'", listed))
assert fields == fc.HOLD_FIELDS, (
    f"sniper/client field mismatch\n  js: {fields}\n  py: {fc.HOLD_FIELDS}")
print(f"hold fields match foreup_client ({len(fields)} fields)")

# Guard the things that make it work in the user's own session.
assert "credentials: 'same-origin'" in js, "must send the user's ForeUp cookies"
assert "localStorage.setItem('pending_reservation'" in js, \
    "must write where ForeUp's own code looks, or the modal ignores the hold"
assert "application/x-www-form-urlencoded" in js, "hold body must be form-encoded"
assert "foreupsoftware.com" in js, "must refuse to run on the wrong site"
print("session, localStorage, encoding, and host guard all present")

# No release time -> starts immediately rather than waiting forever.
assert json.loads(re.search(r"const CFG = (\{.*?\});", sniper_js(JOB), re.S)
                  .group(1))["releaseUtc"] == ""
print("unarmed variant starts immediately")

print("\nPASS")
