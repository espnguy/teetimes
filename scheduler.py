"""
Background scheduler — polls ForeUp and updates job state in Postgres.
"""

import uuid
import logging
import threading
import time
import os
from datetime import datetime
from foreup_client import ForeUpClient, parse_course_url
from notifier import notify_times_available
import db

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 120))


class TeeTimeScheduler:
    def __init__(self):
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self):
        self._running = False

    # ── Public API ────────────────────────────────────────────────────────────

    def add_job(self, data: dict) -> str:
        job_id = str(uuid.uuid4())[:8]
        job = {
            "id":            job_id,
            "course_id":     data["course_id"],
            "course_name":   data.get("course_name", ""),
            "schedule_id":   data["schedule_id"],
            "booking_class": data.get("booking_class", ""),
            "course_url":    data["course_url"],
            "target_date":   data["target_date"],
            "time_from":     data["time_from"],
            "time_to":       data["time_to"],
            "players":       int(data["players"]),
            "holes":         int(data.get("holes", 18)),
            "status":        "polling",
            "logs":          [],
        }
        db.insert_job(job)
        self._log(job_id,
            f"Job created. Polling every {POLL_INTERVAL}s for "
            f"{data['target_date']} {data['time_from']}–{data['time_to']} "
            f"({data['players']} players)"
        )
        return job_id

    def remove_job(self, job_id: str):
        db.delete_job(job_id)

    def get_job(self, job_id: str) -> dict | None:
        return db.load_job(job_id)

    def get_all_jobs(self) -> list[dict]:
        return db.load_all_jobs()

    def mark_job_booked(self, job_id: str, confirmation: dict):
        db.update_job_fields(job_id, {
            "status": "booked",
            "booked_confirmation": confirmation,
        })
        self._log(job_id, f"✅ BOOKED! Confirmation: {str(confirmation)[:200]}")

    def get_job_logs(self, job_id: str) -> list[str]:
        job = db.load_job(job_id)
        return job.get("logs", []) if job else []

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            try:
                jobs = db.load_all_jobs()
                for job in jobs:
                    if job["status"] in ("polling", "available"):
                        self._poll_job(job["id"])
            except Exception as e:
                logger.exception(f"Poll loop error: {e}")
            time.sleep(POLL_INTERVAL)

    def _poll_job(self, job_id: str):
        job = db.load_job(job_id)
        if not job:
            return

        cfg = db.load_config()
        email    = cfg.get("email")
        password = cfg.get("password")

        if not email or not password:
            self._log(job_id, "⚠️ No credentials configured — skipping poll. Go to Settings.")
            return

        try:
            client = ForeUpClient(email, password)
            times = client.fetch_tee_times(
                course_id=job["course_id"],  # also used for session init
                schedule_id=job["schedule_id"],
                date=job["target_date"],
                time_from=job["time_from"],
                time_to=job["time_to"],
                players=job["players"],
                holes=job.get("holes", 18),
                booking_class=job.get("booking_class", ""),
            )

            now_str = datetime.now().strftime("%H:%M:%S")
            db.update_job_fields(job_id, {
                "last_polled":     datetime.now(),
                "available_times": times,
            })

            if times:
                # Sort by time and pick the earliest
                earliest = sorted(times, key=lambda t: t.get("time", ""))[0]
                self._log(job_id,
                    f"🟢 [{now_str}] {len(times)} time(s) available! "
                    f"Earliest: {earliest.get('time')} — auto-booking now..."
                )
                # Auto-book the earliest time
                try:
                    result = client.book_tee_time(
                        course_id=job["course_id"],
                        schedule_id=job["schedule_id"],
                        time_data=earliest,
                        players=job["players"],
                        booking_class=job.get("booking_class", ""),
                    )
                    db.update_job_fields(job_id, {
                        "status": "booked",
                        "booked_confirmation": result,
                        "available_times": times,
                    })
                    self._log(job_id, f"✅ BOOKED! {earliest.get('time')} for {job['players']} players. Confirmation: {str(result)[:200]}")
                    # Send notification that booking was made
                    notify_times_available(
                        user_token=cfg.get("pushover_user_token", ""),
                        app_token=cfg.get("pushover_app_token", ""),
                        job=job,
                        times=[earliest],
                        dashboard_url=cfg.get("dashboard_url", ""),
                    )
                    self._log(job_id, "📲 Booking confirmation notification sent!")
                except Exception as book_err:
                    # Booking failed — fall back to notify-and-confirm flow
                    self._log(job_id, f"⚠️ Auto-book failed ({book_err}) — notifying you to confirm manually.")
                    db.update_job_fields(job_id, {"status": "available", "available_times": times})
                    already_notified = job.get("notification_sent", False)
                    if not already_notified:
                        sent = notify_times_available(
                            user_token=cfg.get("pushover_user_token", ""),
                            app_token=cfg.get("pushover_app_token", ""),
                            job=job,
                            times=times,
                            dashboard_url=cfg.get("dashboard_url", ""),
                        )
                        db.update_job_fields(job_id, {"notification_sent": sent})
                        if sent:
                            self._log(job_id, "📲 Pushover notification sent — please confirm manually.")
            else:
                if job["status"] == "available":
                    db.update_job_fields(job_id, {
                        "status": "polling",
                        "notification_sent": False,
                    })
                self._log(job_id, f"⏳ [{now_str}] No times in window yet.")

        except PermissionError as e:
            self._log(job_id, f"🔑 Auth error: {e}")
            db.update_job_fields(job_id, {"status": "error"})
        except Exception as e:
            self._log(job_id, f"❌ Poll error: {e}")
            logger.exception(f"Poll error for job {job_id}")

    def _log(self, job_id: str, message: str):
        entry = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — {message}"
        logger.info(f"[job {job_id}] {message}")
        db.append_job_log(job_id, entry)
