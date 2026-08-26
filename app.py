"""
ForeUp Tee Time Auto-Booker
Flask web dashboard + background poller
"""

import os
import logging
from flask import Flask, render_template, jsonify, request, redirect, url_for
from foreup_client import ForeUpClient, parse_course_url
from course_resolver import resolve_course_from_url, RESOLVER_VERSION
from scheduler import TeeTimeScheduler
from notifier import notify_test
from release import compute_release, describe
from sniper import sniper_js
import discovery
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
db.init_db()
scheduler = TeeTimeScheduler()
scheduler.start()
logger.info("Scheduler started at module load")


# ─── Web Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    cfg = db.load_config()
    jobs = scheduler.get_all_jobs()
    courses = db.load_courses()
    return render_template("index.html",
                           config=cfg,
                           jobs=jobs,
                           courses=courses,
                           env_creds=db.credentials_from_env())


@app.route("/config", methods=["POST"])
def update_config():
    data = request.form.to_dict()
    if not data.get("password"):
        data.pop("password", None)
    db.save_config(data)
    return redirect(url_for("index"))


# ─── Course API ───────────────────────────────────────────────────────────────

@app.route("/api/resolve_course", methods=["POST"])
def resolve_course():
    """Auto-detect schedule_id and booking_class from a booking URL, save to library."""
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    try:
        info = resolve_course_from_url(url)
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/discover")
def discover_courses():
    """Find courses near a zip code and report which ones are supported."""
    zip_code = (request.args.get("zip") or "").strip()
    if not zip_code:
        return jsonify({"error": "zip is required"}), 400
    try:
        radius = int(request.args.get("radius", discovery.DEFAULT_RADIUS_MILES))
    except ValueError:
        radius = discovery.DEFAULT_RADIUS_MILES
    radius = max(1, min(radius, 100))

    try:
        result = discovery.discover(zip_code, radius_miles=radius)
    except Exception as e:
        logger.exception("discover failed")
        return jsonify({"error": str(e)}), 500

    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/courses")
def get_courses():
    return jsonify(db.load_courses())


@app.route("/api/release_time", methods=["POST"])
def release_time():
    """
    When does this course's sheet open for a given date?
    Lets the UI show the release moment before a snipe job is armed.
    """
    data = request.json or {}
    url         = (data.get("course_url") or "").strip()
    target_date = (data.get("target_date") or "").strip()
    if not url or not target_date:
        return jsonify({"error": "course_url and target_date are required"}), 400

    try:
        course = resolve_course_from_url(url)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    release = compute_release(
        target_date=target_date,
        online_open_time=course.get("online_open_time", ""),
        course_timezone=course.get("timezone", ""),
    )
    if release is None:
        return jsonify({
            "available": False,
            "reason": "This course does not publish an online open time, so the "
                      "release moment cannot be derived.",
        })
    return jsonify({
        "available":   True,
        "release_utc": release.isoformat(),
        "description": describe(release, course.get("timezone", "")),
        "timezone":    course.get("timezone", ""),
        "open_time":   course.get("online_open_time", ""),
    })


@app.route("/api/courses/<course_id>", methods=["PUT"])
def update_course(course_id):
    """Manually save or update a course (for when auto-detect fails)."""
    data = request.json
    if not data.get("name"):
        return jsonify({"error": "Missing: name"}), 400
    platform = data.get("platform", "foreup")
    info = {
        "course_id":     course_id,
        "schedule_id":   data.get("schedule_id", course_id),
        "booking_class": data.get("booking_class", course_id),
        "name":          data["name"],
        "url":           data.get("url", ""),
        "platform":      platform,
        "be_alias":      data.get("be_alias", ""),
        # Stamp as current so a hand-entered override is never silently
        # overwritten by auto-detection on the next lookup.
        "resolver_version": RESOLVER_VERSION,
        "online_open_time": data.get("online_open_time", ""),
        "timezone":         data.get("timezone", ""),
    }
    db.save_course(course_id, info)
    return jsonify({"success": True, "course": info})


@app.route("/api/courses/<course_id>/redetect", methods=["POST"])
def redetect_course(course_id):
    """Force a fresh detection for a saved course, ignoring the cached row."""
    courses = db.load_courses()
    saved = courses.get(course_id)
    if not saved:
        return jsonify({"error": "Course not found"}), 404
    url = saved.get("url") or ""
    if not url:
        return jsonify({"error": "This course has no URL stored to re-detect from."}), 400
    try:
        info = resolve_course_from_url(url, force=True)
        return jsonify({"success": True, "course": info})
    except Exception as e:
        logger.exception(f"redetect failed for {course_id}")
        return jsonify({"error": str(e)}), 400


@app.route("/api/courses/<course_id>", methods=["DELETE"])
def remove_course(course_id):
    db.delete_course(course_id)
    return jsonify({"success": True})


# ─── Job API ──────────────────────────────────────────────────────────────────

@app.route("/api/add_job", methods=["POST"])
def add_job():
    data = request.json
    required = ["course_url", "target_date", "time_from", "time_to", "players"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400
    try:
        # Auto-resolve course IDs (uses cache if already saved)
        course = resolve_course_from_url(data["course_url"])
        data["course_id"]     = course["course_id"]
        data["schedule_id"]   = course["schedule_id"]
        data["booking_class"] = course["booking_class"]
        data["course_name"]   = course["name"]
        data["be_alias"]      = course.get("be_alias", "")
        # Detect platform from URL if not stored on the course
        stored_platform = course.get("platform")
        if not stored_platform or stored_platform == "foreup":
            from course_resolver import detect_platform
            detected = detect_platform(data["course_url"])
            data["platform"] = detected if detected != "unknown" else "foreup"
        else:
            data["platform"] = stored_platform

        # Snipe mode — arm a burst poll for the instant the sheet opens.
        release_note = None
        if data.get("snipe"):
            release = compute_release(
                target_date=data["target_date"],
                online_open_time=course.get("online_open_time", ""),
                course_timezone=course.get("timezone", ""),
            )
            if release is None:
                return jsonify({"error":
                    "Cannot arm snipe: this course does not publish an online "
                    "open time and timezone. Re-resolve the course, or add the "
                    "job without snipe mode."}), 400
            data["snipe_at"] = release
            release_note = describe(release, course.get("timezone", ""))

        job_id = scheduler.add_job(data)
        return jsonify({"success": True, "job_id": job_id, "course": course,
                        "release": release_note})
    except Exception as e:
        logger.exception("add_job failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/jobs/<job_id>/release_hold", methods=["POST"])
def release_hold(job_id):
    """
    Hand a parked tee time back to the sheet so the user can book it.

    Time-critical: the slot is open to everyone the moment this returns, so the
    response carries the booking link the caller should already be sitting on.
    """
    try:
        parked = scheduler.release_hold(job_id)
        job = scheduler.get_job(job_id) or {}
        return jsonify({
            "success": True,
            "hold": parked,
            "booking_url": ForeUpClient.booking_url(
                job.get("course_id", ""), job.get("target_date", ""),
                int(job.get("players", 2) or 2)),
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.exception(f"release_hold failed for {job_id}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/jobs/<job_id>/sniper")
def job_sniper(job_id):
    """
    The in-browser sniper for this watch.

    A hold placed server-side is scoped to the server's ForeUp session and reads
    as unavailable in your own browser, so the hold has to run in your tab. This
    returns a self-contained script to paste into the console there.
    """
    job = scheduler.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.get("platform", "foreup") != "foreup":
        return jsonify({"error": "The browser sniper only supports ForeUp courses."}), 400

    snipe_at = job.get("snipe_at")
    release_iso = snipe_at if isinstance(snipe_at, str) else (
        snipe_at.isoformat() if snipe_at else None)

    return jsonify({
        "script":      sniper_js(job, release_iso),
        "booking_url": ForeUpClient.booking_url(
            job["course_id"], job["target_date"], int(job["players"])),
        "release":     release_iso,
    })


@app.route("/api/remove_job/<job_id>", methods=["DELETE"])
def remove_job(job_id):
    scheduler.remove_job(job_id)
    return jsonify({"success": True})


@app.route("/api/jobs")
def get_jobs():
    return jsonify(scheduler.get_all_jobs())


@app.route("/api/available_times/<job_id>")
def get_available_times(job_id):
    job = scheduler.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    cfg = db.load_config()
    try:
        client = ForeUpClient(cfg.get("email"), cfg.get("password"))
        times = client.fetch_tee_times(
            course_id=job["course_id"],
            schedule_id=job["schedule_id"],
            date=job["target_date"],
            time_from=job["time_from"],
            time_to=job["time_to"],
            players=int(job["players"]),
            booking_class=job.get("booking_class", ""),
        )
        return jsonify({"times": times})
    except Exception as e:
        logger.exception(f"get_available_times failed for job {job_id}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/test_pushover", methods=["POST"])
def test_pushover():
    data = request.json
    success = notify_test(
        user_token=data.get("pushover_user_token", ""),
        app_token=data.get("pushover_app_token", ""),
    )
    if success:
        return jsonify({"success": True, "message": "Notification sent! Check your phone."})
    return jsonify({"success": False, "message": "Failed — check your Pushover tokens."}), 400


@app.route("/api/test_login", methods=["POST"])
def test_login():
    data = request.json
    try:
        client = ForeUpClient(data.get("email"), data.get("password"))
        client.login()
        return jsonify({"success": True, "message": "Login successful!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 401


@app.route("/api/logs/<job_id>")
def get_job_logs(job_id):
    return jsonify(scheduler.get_job_logs(job_id))


@app.route("/api/scheduler_status")
def scheduler_status():
    alive = scheduler._thread is not None and scheduler._thread.is_alive()
    jobs = scheduler.get_all_jobs()
    return jsonify({
        "scheduler_running": alive,
        "job_count": len(jobs),
        "jobs": [{"id": j["id"], "status": j["status"], "last_polled": j.get("last_polled")} for j in jobs]
    })


# ─── Error handlers ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    logger.exception("Unhandled 500 error")
    return jsonify({"error": "Internal server error", "detail": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
