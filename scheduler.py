"""
Background scheduler — polls ForeUp and updates job state in Postgres.
"""

import uuid
import logging
import threading
import time
import os
from datetime import datetime, timezone, timedelta
from foreup_client import ForeUpClient, parse_course_url, HOLD_SECONDS
from notifier import notify_times_available
import db

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 120))

# Snipe mode — for courses that release their sheet at a fixed moment (Grapevine
# opens at 06:00 local). A 120s poll can miss the good times by two minutes, so
# around the release instant we burst-poll instead.
# Main loop granularity. Comfortably finer than SNIPE_LEAD_SECONDS, so a burst
# is never started late, without hammering Postgres with a query every second.
TICK                 = 5.0
SNIPE_LEAD_SECONDS   = 20     # start bursting this long before the release
SNIPE_BURST_INTERVAL = 0.25   # ~4 requests/sec while the window is open
SNIPE_WINDOW_SECONDS = 120    # give up this long after the release

# Parking a found time. The server claims the slot so nobody else can take it
# while you get to a browser, then hands it back on your say-so and you book it
# normally. Deliberately NOT refreshed: ForeUp's own limit is 5 minutes and this
# stays inside it, so a slot is never off the sheet longer than a human sitting
# in checkout would hold it.
PARK_ENABLED = os.environ.get("PARK_HOLDS", "true").lower() == "true"



class TeeTimeScheduler:
    def __init__(self):
        self._thread = None
        self._running = False
        self._next_poll = {}      # job_id -> monotonic deadline
        self._snipe_threads = {}  # job_id -> live burst thread

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
            "platform":      data.get("platform", "foreup"),
            "status":        "polling",
            "snipe_at":      data.get("snipe_at") or None,
            "logs":          [],
        }
        db.insert_job(job)
        self._log(job_id,
            f"Job created. Polling every {POLL_INTERVAL}s for "
            f"{data['target_date']} {data['time_from']}–{data['time_to']} "
            f"({data['players']} players)"
        )
        if job["snipe_at"]:
            self._log(job_id,
                f"🎯 Snipe armed for {job['snipe_at']} — will burst-poll from "
                f"{SNIPE_LEAD_SECONDS}s before release."
            )
        return job_id

    def remove_job(self, job_id: str):
        db.delete_job(job_id)
        self._next_poll.pop(job_id, None)

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

    def _is_expired(self, job: dict) -> bool:
        """Return True if the job's target date is in the past."""
        from datetime import datetime, date
        try:
            target = datetime.strptime(job["target_date"], "%m-%d-%Y").date()
            return target < date.today()
        except Exception:
            return False

    @staticmethod
    def _snipe_at(job: dict):
        """Parse the job's snipe_at into an aware datetime, or None."""
        raw = job.get("snipe_at")
        if not raw:
            return None
        if isinstance(raw, datetime):
            dt = raw
        else:
            try:
                dt = datetime.fromisoformat(str(raw))
            except ValueError:
                return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _snipe_phase(self, job: dict) -> str:
        """
        Where is this job relative to its release window?
        'none' (not a snipe job), 'waiting', 'burst', or 'over'.
        """
        target = self._snipe_at(job)
        if target is None:
            return "none"
        delta = (datetime.now(timezone.utc) - target).total_seconds()
        if delta < -SNIPE_LEAD_SECONDS:
            return "waiting"
        if delta <= SNIPE_WINDOW_SECONDS:
            return "burst"
        return "over"

    def _poll_loop(self):
        """
        Tick fast, poll each job only when it is due.

        Regular jobs run every POLL_INTERVAL. Snipe jobs switch to
        SNIPE_BURST_INTERVAL for the window around their release time.
        """
        while self._running:
            try:
                for job in db.load_all_jobs():
                    if job["status"] not in ("polling", "available"):
                        continue

                    job_id = job["id"]
                    if self._is_expired(job):
                        db.update_job_fields(job_id, {"status": "expired"})
                        self._log(job_id, f"⏰ Job expired — {job['target_date']} has passed.")
                        self._next_poll.pop(job_id, None)
                        continue

                    phase = self._snipe_phase(job)

                    if phase == "burst":
                        # Hand off to a dedicated thread so the burst runs at its
                        # own cadence instead of this loop's, and without a DB
                        # round-trip per attempt.
                        if job_id not in self._snipe_threads:
                            t = threading.Thread(
                                target=self._snipe_run, args=(job_id,), daemon=True)
                            self._snipe_threads[job_id] = t
                            t.start()
                        continue

                    if job_id in self._snipe_threads:
                        continue  # burst thread owns this job right now

                    if phase == "over":
                        # Only reachable if the app restarted after the window
                        # closed. Clear the arm so the UI stops advertising it.
                        db.update_job_fields(job_id, {"snipe_at": None})
                        self._log(job_id,
                            "🎯 Release window already passed — back to normal polling.")

                    now = time.monotonic()
                    if now < self._next_poll.get(job_id, 0.0):
                        continue
                    self._next_poll[job_id] = now + POLL_INTERVAL
                    self._poll_job(job_id)
            except Exception as e:
                logger.exception(f"Poll loop error: {e}")
            time.sleep(TICK)

    # ── Snipe mode ────────────────────────────────────────────────────────────

    def _snipe_run(self, job_id: str):
        """
        Burst-poll a single job across its release window.

        Runs in its own thread at SNIPE_BURST_INTERVAL, reusing one client (and
        one authenticated session) for every attempt so each retry is a single
        HTTP round-trip. Stops as soon as times appear.
        """
        try:
            job = db.load_job(job_id)
            if not job:
                return

            target = self._snipe_at(job)
            deadline = target.timestamp() + SNIPE_WINDOW_SECONDS
            cfg = db.load_config()
            client = self._client_for(job, cfg)

            # Establish the session now, not on the first hold — that fetch cost
            # ~900ms when measured, which is the whole race.
            if hasattr(client, "prewarm"):
                try:
                    client.prewarm(job["course_id"])
                except Exception as e:
                    self._log(job_id, f"⚠️ Could not pre-warm session: {e}")

            self._log(job_id,
                f"🎯 Release window open — burst-polling every "
                f"{SNIPE_BURST_INTERVAL}s until {int(SNIPE_WINDOW_SECONDS)}s past release.")

            attempts = 0
            while self._running and time.time() < deadline:
                attempts += 1
                try:
                    times = self._fetch(client, job)
                except Exception as e:
                    # Transient errors are expected while the sheet is flipping.
                    if attempts % 40 == 0:
                        self._log(job_id, f"… {attempts} attempts, last error: {e}")
                    times = []

                if times:
                    self._log(job_id,
                        f"🎯 Sheet opened after {attempts} attempt(s) — "
                        f"{len(times)} time(s) in window.")
                    self._handle_snipe_hit(job, times, cfg, client)
                    return

                time.sleep(SNIPE_BURST_INTERVAL)

            self._log(job_id,
                f"🎯 Release window closed after {attempts} attempts with nothing in "
                f"{job['time_from']}–{job['time_to']}. Reverting to normal polling.")
            db.update_job_fields(job_id, {"snipe_at": None})
        except Exception as e:
            logger.exception(f"Snipe run failed for job {job_id}")
            self._log(job_id, f"❌ Snipe error: {e}")
        finally:
            self._snipe_threads.pop(job_id, None)

    def _handle_snipe_hit(self, job: dict, times: list, cfg: dict, client):
        """
        Times appeared during the release window — park the best one and alert.

        A parked slot cannot be handed to your browser: ForeUp scopes a pending
        reservation to the session that made it, so it reads as "no longer
        available" to you exactly as it does to everyone else — verified, even
        with the server logged in as your own account. What parking buys is
        *time*: nobody else can take it either, so you can reach a browser and
        then hit Release to put it back and book it immediately.
        """
        job_id = job["id"]
        # Disarm before doing anything else. The release window stays "burst" for
        # up to SNIPE_WINDOW_SECONDS after a hit, and without this the poll loop
        # re-enters and fires again on every pass.
        db.update_job_fields(job_id, {
            "status":          "available",
            "available_times": times,
            "last_polled":     datetime.now(),
            "snipe_at":        None,
        })

        parked = self._park(job, times, client)

        sent = notify_times_available(
            user_token=cfg.get("pushover_user_token", ""),
            app_token=cfg.get("pushover_app_token", ""),
            job=job,
            times=times,
            dashboard_url=cfg.get("dashboard_url", ""),
            urgent=True,
            parked=parked,
        )
        db.update_job_fields(job_id, {"notification_sent": sent})
        self._log(job_id,
            "📲 Notification sent." if sent else "⚠️ Pushover not configured.")

    def _park(self, job: dict, times: list, client) -> dict | None:
        """
        Claim the best matching slot so nobody else takes it while you get to a
        browser. You then hit Release and book it normally.

        A parked slot is NOT refreshed — it lapses on ForeUp's own 5 minute
        timer if you never respond, so it is never off the sheet longer than a
        person sitting in checkout would hold it.
        """
        if not PARK_ENABLED or job.get("platform", "foreup") != "foreup":
            return None
        job_id = job["id"]
        best = times[0]     # earliest slot inside the requested window
        try:
            t0 = time.time()
            raw = client.hold(best, players=job["players"], holes=job.get("holes", 18))
            ms = (time.time() - t0) * 1000
            reservation_id = raw.get("reservation_id") or raw.get("id")
            if not reservation_id:
                self._log(job_id, f"⚠️ Hold returned no reservation id: {raw}")
                return None

            now = datetime.now(timezone.utc)
            parked = {
                "reservation_id": reservation_id,
                "time":           best.get("time"),
                "green_fee":      best.get("green_fee"),
                "players":        job["players"],
                "held_at":        now.isoformat(),
                "expires_at":     (now + timedelta(
                                      seconds=HOLD_SECONDS)).isoformat(),
                "released":       False,
            }
            db.update_job_fields(job_id, {"hold_result": parked})
            self._log(job_id,
                f"🅿️ PARKED {best.get('time')} for {job['players']} in {ms:.0f}ms "
                f"({reservation_id}). Nobody else can take it for "
                f"{HOLD_SECONDS // 60} min — hit Release when you're "
                f"at the booking page.")
            return parked
        except PermissionError as e:
            self._log(job_id, f"🔓 Could not park (booking class restricted): {e}")
        except Exception as e:
            self._log(job_id, f"🔓 Park attempt failed: {e}")
        return None

    def release_hold(self, job_id: str) -> dict:
        """
        Hand a parked slot back so you can book it.

        The moment this returns, the time is on the sheet for everyone — so the
        dashboard only offers it once you say you are ready, and points you
        straight at the booking page.
        """
        job = db.load_job(job_id)
        if not job:
            raise ValueError("Job not found")
        parked = job.get("hold_result") or {}
        reservation_id = parked.get("reservation_id")
        if not reservation_id:
            raise ValueError("Nothing is parked for this job")
        if parked.get("released"):
            return parked

        client = self._client_for(job, db.load_config())
        client.prewarm(job["course_id"])
        ok = client.release_hold(reservation_id)
        updated = {**parked, "released": True, "release_ok": ok,
                   "released_at": datetime.now(timezone.utc).isoformat()}
        db.update_job_fields(job_id, {"hold_result": updated})
        self._log(job_id,
            f"🏁 Released {parked.get('time')} — it is back on the sheet NOW. Go.")
        return updated

    @staticmethod
    def _client_for(job: dict, cfg: dict):
        """
        Build the right client for a job's platform.

        ForeUp public booking classes serve availability without a login, so
        missing credentials are not fatal — only restricted classes will 401.
        """
        if job.get("platform", "foreup") in ("teeitup", "golfnow"):
            from golfnow_client import GolfNowClient
            return GolfNowClient()
        return ForeUpClient(cfg.get("email"), cfg.get("password"))

    @staticmethod
    def _fetch(client, job: dict) -> list:
        """One availability request for a job, using an already-built client."""
        platform = job.get("platform", "foreup")
        extra = {}
        if platform in ("teeitup", "golfnow"):
            extra["platform"] = platform
            # Pass be_alias for X-Be-Alias header (TeeItUp/Kenna)
            if job.get("be_alias"):
                extra["be_alias"] = job["be_alias"]

        return client.fetch_tee_times(
            course_id=job["course_id"],
            schedule_id=job["schedule_id"],
            date=job["target_date"],
            time_from=job["time_from"],
            time_to=job["time_to"],
            players=job["players"],
            holes=job.get("holes", 18),
            booking_class=job.get("booking_class", ""),
            **extra,
        )

    def _poll_job(self, job_id: str):
        job = db.load_job(job_id)
        if not job:
            return

        cfg = db.load_config()

        try:
            client = self._client_for(job, cfg)
            times = self._fetch(client, job)

            now_str = datetime.now().strftime("%H:%M:%S")
            db.update_job_fields(job_id, {
                "last_polled":     datetime.now(),
                "available_times": times,
            })

            if times:
                already_notified = job.get("notification_sent", False)
                db.update_job_fields(job_id, {
                    "status": "available",
                    "available_times": times,
                })
                self._log(job_id,
                    f"🟢 [{now_str}] {len(times)} time(s) available! "
                    f"Earliest: {times[0].get('time')}"
                )
                if not already_notified:
                    # A cancellation that pops up mid-week is the same race as a
                    # 6am release, just quieter — park it so it is still there
                    # when you reach a browser.
                    parked = self._park(job, times, client)
                    sent = notify_times_available(
                        user_token=cfg.get("pushover_user_token", ""),
                        app_token=cfg.get("pushover_app_token", ""),
                        job=job,
                        times=times,
                        dashboard_url=cfg.get("dashboard_url", ""),
                        parked=parked,
                    )
                    db.update_job_fields(job_id, {"notification_sent": sent})
                    if sent:
                        self._log(job_id, "📲 Pushover notification sent with booking link!")
                    else:
                        self._log(job_id, "⚠️ Pushover not configured.")
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
