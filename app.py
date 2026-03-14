"""
ForeUp Tee Time Auto-Booker
Flask web dashboard + background poller
"""

import os
import json
import time
import threading
import logging
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, redirect, url_for
from foreup_client import ForeUpClient, parse_course_url
from scheduler import TeeTimeScheduler
from notifier import notify_test
from config import load_config, save_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
scheduler = TeeTimeScheduler()


# ─── Web Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    cfg = load_config()
    jobs = scheduler.get_all_jobs()
    return render_template("index.html", config=cfg, jobs=jobs)


@app.route("/config", methods=["POST"])
def update_config():
    data = request.form.to_dict()
    save_config(data)
    return redirect(url_for("index"))


# ─── API Endpoints ────────────────────────────────────────────────────────────

@app.route("/api/add_job", methods=["POST"])
def add_job():
    """Add a new polling job."""
    data = request.json
    required = ["course_url", "target_date", "time_from", "time_to", "players"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400
    job_id = scheduler.add_job(data)
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/remove_job/<job_id>", methods=["DELETE"])
def remove_job(job_id):
    scheduler.remove_job(job_id)
    return jsonify({"success": True})


@app.route("/api/jobs")
def get_jobs():
    return jsonify(scheduler.get_all_jobs())


@app.route("/api/available_times/<job_id>")
def get_available_times(job_id):
    """Fetch current available tee times for a job (for confirmation UI)."""
    job = scheduler.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    cfg = load_config()
    try:
        client = ForeUpClient(cfg.get("email"), cfg.get("password"))
        times = client.fetch_tee_times(
            course_id=job["course_id"],
            schedule_id=job["schedule_id"],
            date=job["target_date"],
            time_from=job["time_from"],
            time_to=job["time_to"],
            players=int(job["players"]),
        )
        return jsonify({"times": times})
    except Exception as e:
        logger.error(f"Error fetching times for job {job_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/book", methods=["POST"])
def book_tee_time():
    """Manually confirm and book a specific tee time."""
    data = request.json
    job_id = data.get("job_id")
    time_data = data.get("time_data")

    if not job_id or not time_data:
        return jsonify({"error": "Missing job_id or time_data"}), 400

    job = scheduler.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    cfg = load_config()
    try:
        client = ForeUpClient(cfg.get("email"), cfg.get("password"))
        result = client.book_tee_time(
            course_id=job["course_id"],
            schedule_id=job["schedule_id"],
            time_data=time_data,
            players=int(job["players"]),
        )
        scheduler.mark_job_booked(job_id, result)
        return jsonify({"success": True, "confirmation": result})
    except Exception as e:
        logger.error(f"Booking error for job {job_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/test_pushover", methods=["POST"])
def test_pushover():
    """Send a test Pushover notification."""
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
    """Verify credentials work."""
    data = request.json
    try:
        client = ForeUpClient(data.get("email"), data.get("password"))
        client.login()
        return jsonify({"success": True, "message": "Login successful!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 401


@app.route("/api/resolve_course", methods=["POST"])
def resolve_course():
    """Extract course_id and schedule_id from a ForeUp booking URL."""
    data = request.json
    url = data.get("url", "")
    try:
        from foreup_client import parse_course_url
        info = parse_course_url(url)
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/logs/<job_id>")
def get_job_logs(job_id):
    return jsonify(scheduler.get_job_logs(job_id))


if __name__ == "__main__":
    scheduler.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
