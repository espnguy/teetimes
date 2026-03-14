# ⛳ ForeUp Tee Time Auto-Booker

> A self-hosted web dashboard that watches ForeUp golf courses 24/7, alerts you the instant a tee time opens up, and lets you confirm and book with one click.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?style=flat&logo=flask)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Railway-336791?style=flat&logo=postgresql)
![Deploy on Railway](https://img.shields.io/badge/Deploy-Railway-8B5CF6?style=flat&logo=railway)

---

## Features

- 🔄 **Automatic polling** — checks ForeUp every 2 minutes while you do other things
- 📲 **Pushover push notifications** — instant phone alert the moment times open up
- ✅ **You confirm before booking** — see all available slots, pick one, tap Book
- 💾 **Persistent storage** — jobs, courses, and settings survive restarts via PostgreSQL
- 🏌️ **Saved course library** — set up a course once, reuse it forever with one click
- 🔍 **Auto-detect course IDs** — paste a booking URL and the app scrapes schedule_id and booking_class automatically
- 📱 **Web dashboard** — access from your phone, tablet, or laptop anywhere

---

## How it works

1. Paste a ForeUp booking URL and set your target date + time window
2. The server polls ForeUp every 2 minutes (configurable)
3. When a slot opens in your window, you get a Pushover push notification
4. Open the dashboard, see all available times, pick one, confirm
5. The reservation is made under your own ForeUp account

---

## Tech Stack

| Layer | Tech |
|---|---|
| Web framework | Flask + Gunicorn |
| Background polling | Python daemon thread |
| Database | PostgreSQL (Railway) |
| Notifications | Pushover API |
| Deployment | Railway |

---

## Project Structure

```
teetime-booker/
├── app.py               ← Flask routes + REST API
├── db.py                ← PostgreSQL data layer (config, courses, jobs tables)
├── scheduler.py         ← Background polling thread (starts at module load)
├── foreup_client.py     ← ForeUp HTTP client (session init, login, fetch, book)
├── course_resolver.py   ← Auto-detects schedule_id/booking_class from booking page
├── notifier.py          ← Pushover push notifications
├── config.py            ← Legacy config stub (superseded by db.py)
├── templates/
│   └── index.html       ← Single-page dashboard UI
├── requirements.txt
├── Procfile             ← gunicorn --workers 1 --threads 4
└── README.md
```

---

## Deployment (Railway)

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/YOUR_USERNAME/teetime-booker.git
git push -u origin main
```

### 2. Create Railway project

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
2. Select your repo — Railway detects the `Procfile` automatically

### 3. Add PostgreSQL

1. In your Railway project → **+ New** → **Database** → **PostgreSQL**
2. Click your **web service** → **Variables** tab → **+ Add Variable Reference**
3. Select your Postgres service → select `DATABASE_URL` → **Add**

### 4. Set environment variables

In your web service → **Variables** tab, add:

| Variable | Value |
|---|---|
| `FOREUP_EMAIL` | your ForeUp login email |
| `FOREUP_PASSWORD` | your ForeUp password |
| `PUSHOVER_USER_TOKEN` | your Pushover user key |
| `PUSHOVER_APP_TOKEN` | your Pushover app token |
| `DASHBOARD_URL` | your Railway app URL (for notification deep links) |
| `POLL_INTERVAL` | `120` (seconds between polls, default 2 min) |

> **Important:** Set credentials as Railway environment variables, not in the app UI. The filesystem resets on every redeploy so anything saved to files is lost. Env vars persist forever.

### 5. Deploy

Railway auto-deploys on every push to `main`. Your app will be live at `https://your-app.railway.app`.

---

## Quick Start (Local)

```bash
cd teetime-booker
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Set required env vars
export DATABASE_URL="postgresql://..."
export FOREUP_EMAIL="you@email.com"
export FOREUP_PASSWORD="yourpassword"

python app.py
```

Open **http://localhost:5000**

---

## Using the Dashboard

### First time setup
1. Open the dashboard → click **⚙️ Settings**
2. Verify your credentials are shown (pulled from env vars)
3. Enter your Pushover keys and your dashboard URL → **Save**
4. Click **📲 Test Notification** to confirm Pushover works
5. Click **🔑 Test Login** to verify your ForeUp credentials work

### Adding a watch
1. Paste your course's ForeUp booking URL (e.g. `https://foreupsoftware.com/index.php/booking/19536`)
2. The app auto-detects the course's `schedule_id` and `booking_class` from the page
3. If auto-detect fails, a yellow warning box appears — enter the IDs manually from DevTools
4. Pick your target date and preferred time window (e.g. 7:00 AM – 10:00 AM)
5. Choose number of players → **Start Watching**

The course is saved to your library automatically — next time just pick it from the dropdown.

### When times are found
- Job card turns 🟢 **AVAILABLE**
- You receive a Pushover notification with available times listed and a link to the dashboard
- Click **View & Confirm** → select a time slot → **Book Selected Time**

---

## Finding Your Course Details

If auto-detect fails, you can get the IDs manually:

1. Open your course's ForeUp booking URL in Chrome
2. Press **F12** → **Network** tab
3. Click a date in the booking calendar
4. Find the request to `/api/booking/times` — the URL contains `schedule_id=XXXX&booking_class=XXXX`

---

## How ForeUp Authentication Works

ForeUp uses session-based auth with a CSRF-style check:

1. **Visit booking page** → server issues a `PHPSESSID` session cookie
2. **POST login** with form-encoded credentials + session cookie
3. **All subsequent requests** include the session cookie automatically

The app replicates this flow using `requests.Session()`. Confirmed working endpoints:

| Action | Method | Endpoint |
|---|---|---|
| Init session | `GET` | `/index.php/booking/{course_id}` |
| Login | `POST` | `/index.php/api/booking/users/login` |
| Fetch times | `GET` | `/index.php/api/booking/times` |
| Reserve | `POST` | `/index.php/api/booking/pending_reservation` |

Required request headers (confirmed via Chrome DevTools):
- `Api-Key: no_limits`
- `X-Fu-Golfer-Location: foreup`
- `X-Requested-With: XMLHttpRequest`
- `Content-Type: application/x-www-form-urlencoded; charset=UTF-8` (login only)

---

## Database Schema

Three tables, created automatically on startup via `db.init_db()`:

```sql
-- App settings and credentials
config (key TEXT PRIMARY KEY, value TEXT)

-- Saved golf courses
courses (course_id, schedule_id, booking_class, name, url, created_at)

-- Active polling jobs
jobs (id, course_id, course_name, schedule_id, booking_class, course_url,
      target_date, time_from, time_to, players, holes, status,
      available_times JSONB, booked_confirmation JSONB,
      notification_sent, last_polled, created_at, logs JSONB)
```

Job status flow: `polling` → `available` → `booked`

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Dashboard UI |
| `POST` | `/config` | Save settings |
| `POST` | `/api/add_job` | Start a new watch |
| `DELETE` | `/api/remove_job/<id>` | Remove a watch |
| `GET` | `/api/jobs` | List all jobs |
| `GET` | `/api/available_times/<id>` | Fetch current available times |
| `POST` | `/api/book` | Confirm and book a time |
| `POST` | `/api/resolve_course` | Auto-detect course IDs from URL |
| `GET` | `/api/courses` | List saved courses |
| `PUT` | `/api/courses/<id>` | Save/update a course manually |
| `DELETE` | `/api/courses/<id>` | Remove a saved course |
| `POST` | `/api/test_login` | Verify ForeUp credentials |
| `POST` | `/api/test_pushover` | Send a test notification |
| `GET` | `/api/scheduler_status` | Check polling thread health |
| `GET` | `/api/logs/<job_id>` | Get logs for a job |

---

## Pushover Setup

1. Download the **Pushover** app on your phone ($5 one-time, iOS/Android)
2. Sign up at [pushover.net](https://pushover.net) — your **User Key** is on the dashboard
3. Create an app token at [pushover.net/apps/build](https://pushover.net/apps/build)
4. Enter both keys in the app Settings → **📲 Test Notification** to verify

Notifications are sent at **priority 1** (bypasses quiet hours) so you won't miss a tee time dropping at midnight.

---

## Pro Tips

> **ForeUp typically releases tee times exactly 7 days in advance at midnight.**
> Start your watch the evening before so it's running when slots drop.

- Run **multiple watches** simultaneously for different courses or dates
- Check the **Logs** button on any job to see a timestamped poll history
- If a slot is grabbed before you confirm, the job automatically returns to polling
- Don't set `POLL_INTERVAL` below 60 — be respectful to the course's servers
- Visit `/api/scheduler_status` to verify the polling thread is alive after deploy

---

## Troubleshooting

**Login failing (403)?**
Check that `FOREUP_EMAIL` and `FOREUP_PASSWORD` are set correctly in Railway Variables with no extra spaces or quotes.

**Login failing ("Refresh required")?**
The app now visits the booking page first to get a session cookie before logging in. Make sure you're on v14+.

**Polling shows no times?**
ForeUp may not have released times for that date yet. Times typically drop exactly 7 days in advance. The job will keep polling.

**Start Watching button does nothing?**
Open browser DevTools (F12) → Console tab and check for red errors. Likely a JavaScript issue — make sure you're on the latest version.

**App crashes on startup?**
Almost always a missing `DATABASE_URL`. Check Railway Variables and make sure the Postgres plugin's `DATABASE_URL` is linked to your web service.

**Booking fails after selecting a time?**
The slot may have been taken between the last poll and your confirmation. The job returns to polling automatically.
