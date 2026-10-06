"""
Run the dashboard against an in-memory database, for local UI work only.

Real deployments use PostgreSQL via db.py; this stubs that layer so the app
boots without a database. Seeded with a deliberately stale course row so the
self-healing re-detection path is easy to exercise by hand.

    python devserver.py    →    http://127.0.0.1:5055
"""

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

import db

CONFIG = {"pushover_user_token": "", "pushover_app_token": "", "dashboard_url": ""}
JOBS, CACHE = {}, {}

# resolver_version 0 with booking_class == schedule_id is what rows written
# before the booking-class fix look like.
COURSES = {
    "19536": {
        "course_id": "19536", "schedule_id": "1832", "booking_class": "1832",
        "name": "Grapevine Golf Course - Online Booking", "platform": "foreup",
        "url": "https://foreupsoftware.com/index.php/booking/19536",
        "be_alias": "", "booking_classes": [], "online_open_time": "",
        "timezone": "", "resolver_version": 0,
    }
}

db.init_db               = lambda: None
db.load_config           = lambda: dict(CONFIG)
db.save_config           = lambda d: CONFIG.update(d)
db.credentials_from_env  = lambda: False
db.load_courses          = lambda: dict(COURSES)
db.save_course           = lambda cid, info: COURSES.__setitem__(cid, info)
db.delete_course         = lambda cid: COURSES.pop(cid, None)
db.load_all_jobs         = lambda: list(JOBS.values())
db.load_job              = lambda jid: JOBS.get(jid)
db.insert_job            = lambda j: JOBS.__setitem__(j["id"], j)
db.update_job_fields     = lambda jid, f: JOBS.get(jid, {}).update(f)
db.delete_job            = lambda jid: JOBS.pop(jid, None)
db.append_job_log        = lambda jid, e: JOBS.get(jid, {}).setdefault("logs", []).append(e)
db.load_platform_cache   = lambda: dict(CACHE)
db.save_platform_cache   = lambda w, p, b="": CACHE.__setitem__(w, {"platform": p, "booking_url": b})

import app as A

# Pick up template edits without a restart.
A.app.jinja_env.auto_reload = True
A.app.config["TEMPLATES_AUTO_RELOAD"] = True

if __name__ == "__main__":
    A.app.run(host="127.0.0.1", port=5055, debug=False, use_reloader=False)
