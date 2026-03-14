"""
Background scheduler that polls ForeUp for tee times.

Each "job" tracks:
  - course_id / schedule_id
  - target_date, time_from, time_to, players
  - status: polling | available | booked | error | paused
  - log of events

The scheduler runs a daemon thread that polls every POLL_INTERVAL seconds.
When a match is found the job status flips to "available" so the dashboard
can display a confirm button.
"""

import uuid
import json
import logging
import threading
import time
import os
from datetime import datetime
from copy import deepcopy
from foreup_client import ForeUpClient, parse_course_url
from config import load_config
from notifier import notify_times_available

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 120))   # seconds between polls
STATE_FILE    = os.environ.get("STATE_FILE", "jobs_state.json")
MAX_LOGS      = 100   # keep last N log entries per job


class TeeTimeScheduler:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._thread: threading.Thread | None = None
        self._running = False
        self._load_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self):
        self._running = False

    def add_job(self, data: dict) -> str:
        """Parse the course URL and create a new polling job."""
        info = parse_course_url(data["course_url"])
        job_id = str(uuid.uuid4())[:8]
        job = {
            "id": job_id,
            "course_url": data["course_url"],
            "course_id": info["course_id"],
            "schedule_id": info["schedule_id"],
            "target_date": data["target_date"],       # MM-DD-YYYY
            "time_from": data["time_from"],            # HH:MM
            "time_to": data["time_to"],                # HH:MM
            "players": int(data["players"]),
            "holes": int(data.get("holes", 18)),
            "status": "polling",
            "available_times": [],
            "booked_confirmation": None,
            "created_at": datetime.now().isoformat(),
            "last_polled": None,
            "notification_sent": False,
            "logs": [],
        }
        with self._lock:
            self._jobs[job_id] = job
        self._save_state()
        self._log(job_id, f"Job created. Polling every {POLL_INTERVAL}s for {data['target_date']} "
                          f"{data['time_from']}–{data['time_to']} ({data['players']} players)")
        return job_id

    def remove_job(self, job_id: str):
        with self._lock:
            self._jobs.pop(job_id, None)
        self._save_state()

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            return deepcopy(self._jobs.get(job_id))

    def get_all_jobs(self) -> list[dict]:
        with self._lock:
            return [deepcopy(j) for j in self._jobs.values()]

    def mark_job_booked(self, job_id: str, confirmation: dict):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = "booked"
                self._jobs[job_id]["booked_confirmation"] = confirmation
        self._log(job_id, f"✅ BOOKED! Confirmation: {json.dumps(confirmation)[:200]}")
        self._save_state()

    def get_job_logs(self, job_id: str) -> list[str]:
        with self._lock:
            return list(self._jobs.get(job_id, {}).get("logs", []))

    def pause_job(self, job_id: str):
        with self._lock:
            if job_id in self._jobs and self._jobs[job_id]["status"] == "polling":
                self._jobs[job_id]["status"] = "paused"
        self._save_state()

    def resume_job(self, job_id: str):
        with self._lock:
            if job_id in self._jobs and self._jobs[job_id]["status"] == "paused":
                self._jobs[job_id]["status"] = "polling"
        self._save_state()

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            jobs_snapshot = self.get_all_jobs()
            for job in jobs_snapshot:
                if job["status"] in ("polling", "available"):
                    self._poll_job(job["id"])
            time.sleep(POLL_INTERVAL)

    def _poll_job(self, job_id: str):
        job = self.get_job(job_id)
        if not job:
            return

        cfg = load_config()
        email    = cfg.get("email")
        password = cfg.get("password")

        if not email or not password:
            self._log(job_id, "⚠️  No credentials configured – skipping poll. Go to Settings.")
            return

        try:
            client = ForeUpClient(email, password)
            times = client.fetch_tee_times(
                course_id=job["course_id"],
                schedule_id=job["schedule_id"],
                date=job["target_date"],
                time_from=job["time_from"],
                time_to=job["time_to"],
                players=job["players"],
                holes=job.get("holes", 18),
            )

            now_str = datetime.now().strftime("%H:%M:%S")
            with self._lock:
                if job_id in self._jobs:
                    self._jobs[job_id]["last_polled"] = datetime.now().isoformat()
                    self._jobs[job_id]["available_times"] = times

            if times:
                already_notified = False
                with self._lock:
                    if job_id in self._jobs:
                        self._jobs[job_id]["status"] = "available"
                        already_notified = self._jobs[job_id].get("notification_sent", False)
                self._log(job_id, f"🟢 [{now_str}] {len(times)} time(s) available! "
                                  f"First: {times[0].get('time')} — waiting for your confirmation.")

                # Send Pushover notification only once per availability window
                if not already_notified:
                    sent = notify_times_available(
                        user_token=cfg.get("pushover_user_token", ""),
                        app_token=cfg.get("pushover_app_token", ""),
                        job=job,
                        times=times,
                        dashboard_url=cfg.get("dashboard_url", ""),
                    )
                    with self._lock:
                        if job_id in self._jobs:
                            self._jobs[job_id]["notification_sent"] = sent
                    if sent:
                        self._log(job_id, "📲 Pushover notification sent!")
                    else:
                        self._log(job_id, "⚠️  Pushover not configured — open the dashboard manually.")
            else:
                with self._lock:
                    if job_id in self._jobs and self._jobs[job_id]["status"] == "available":
                        # Times disappeared — reset so we notify again if they return
                        self._jobs[job_id]["status"] = "polling"
                        self._jobs[job_id]["notification_sent"] = False
                self._log(job_id, f"⏳ [{now_str}] No times in window yet.")

        except PermissionError as e:
            self._log(job_id, f"🔑 Auth error: {e}")
            with self._lock:
                if job_id in self._jobs:
                    self._jobs[job_id]["status"] = "error"
        except Exception as e:
            self._log(job_id, f"❌ Poll error: {e}")
            logger.exception(f"Poll error for job {job_id}")

        self._save_state()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _log(self, job_id: str, message: str):
        entry = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — {message}"
        logger.info(f"[job {job_id}] {message}")
        with self._lock:
            if job_id in self._jobs:
                logs = self._jobs[job_id].setdefault("logs", [])
                logs.append(entry)
                if len(logs) > MAX_LOGS:
                    self._jobs[job_id]["logs"] = logs[-MAX_LOGS:]

    def _save_state(self):
        try:
            with self._lock:
                data = deepcopy(self._jobs)
            with open(STATE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE) as f:
                self._jobs = json.load(f)
            # Reset "polling" jobs that were mid-poll when server shut down
            for job in self._jobs.values():
                if job["status"] == "polling":
                    pass  # keep polling
            logger.info(f"Loaded {len(self._jobs)} job(s) from state file")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            self._jobs = {}
