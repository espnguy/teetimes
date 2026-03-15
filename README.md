# ⛳ ForeUp Tee Time Auto-Booker

> A self-hosted web dashboard that watches ForeUp golf courses 24/7 and sends you an instant Pushover notification with all available times the moment they open up.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?style=flat&logo=flask)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Railway-336791?style=flat&logo=postgresql)
![Deploy on Railway](https://img.shields.io/badge/Deploy-Railway-8B5CF6?style=flat&logo=railway)

---

## How it works

1. You set up a watch with a ForeUp course URL, target date, and preferred time window
2. The server polls ForeUp every 2 minutes using your credentials
3. The moment times appear in your window, you get a **Pushover push notification** listing every available slot with times, prices, and spots
4. Tap **"Book on ForeUp →"** in the notification or dashboard to open the booking page and reserve manually
5. The job keeps polling and re-notifying if more times appear

> **Note:** ForeUp requires a reCAPTCHA to complete bookings, so the app doesn't book automatically — it finds the times and gets you there as fast as possible.

---

## Features

- 🔄 **Polls every 2 minutes** while you do other things
- 📲 **Pushover notifications** — instant phone alert listing all available times with prices
- 🏌️ **One-tap to book** — notification and dashboard both link directly to the ForeUp booking page
- 💾 **Persists across restarts** — jobs, courses, and settings stored in PostgreSQL
- 🏌️ **Saved course library** — set up a course once, reuse with one click
- 🔍 **Auto-detects course IDs** — paste a booking URL and schedule_id/booking_class are scraped automatically

---

## What a notification looks like

```
⛳ 8 Tee Times — Thu Mar 20
──────────────
3:20 PM  $55.40  2 open
3:30 PM  $55.40  4 open
3:40 PM  $55.40  4 open
3:50 PM  $55.40  4 open
4:00 PM  $55.40  4 open
4:10 PM  $55.40  4 open
4:20 PM  $55.40  4 open
4:30 PM  $55.40  4 open
──────────────
ForeUp → select Public → Thu Mar 20

[Open ForeUp →]
```

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
├── foreup_client.py     ← ForeUp HTTP client (session init, login, fetch times)
├── course_resolver.py   ← Auto-detects schedule_id/booking_class from booking page HTML
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

> ⚠️ This step is required. The app will crash on startup without `DATABASE_URL`.

### 4. Set environment variables

In your web service → **Variables** tab:

| Variable | Value |
|---|---|
| `FOREUP_EMAIL` | your ForeUp login email |
| `FOREUP_PASSWORD` | your ForeUp password |
| `PUSHOVER_USER_TOKEN` | your Pushover user key |
| `PUSHOVER_APP_TOKEN` | your Pushover app token |
| `POLL_INTERVAL` | `120` (seconds between polls, default 2 min) |

> Set credentials as Railway environment variables — not in the app UI. Railway's filesystem resets on every redeploy so anything saved to files is lost. Env vars persist forever.

### 5. Deploy

Railway auto-deploys on every push to `main`.

---

## Quick Start (Local)

```bash
cd teetime-booker
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export DATABASE_URL="postgresql://..."
export FOREUP_EMAIL="you@email.com"
export FOREUP_PASSWORD="yourpassword"

python app.py
```

Open **http://localhost:5000**

---

## Using the Dashboard

### First time setup
1. Open Settings → verify credentials are shown (pulled from env vars)
2. Enter your Pushover keys → **Save**
3. Click **📲 Test Notification** to confirm Pushover is working
4. Click **🔑 Test Login** to verify your ForeUp credentials work

### Adding a watch
1. Paste your course's ForeUp booking URL (e.g. `https://foreupsoftware.com/index.php/booking/19536`)
2. The app auto-detects `schedule_id` and `booking_class` from the page
3. If auto-detect fails, a yellow box appears — enter the IDs manually from DevTools
4. Pick your target date and preferred time window
5. Choose number of players → **Start Watching**

The course saves to your library automatically — next time just pick it from the dropdown.

### When times are found
- Job card turns 🟢 **AVAILABLE**
- You get a Pushover notification listing all available times with prices and spots
- Tap **"Open ForeUp →"** in the notification, or click **"🏌️ Book on ForeUp →"** on the dashboard
- On ForeUp: select your booking class (e.g. Public), navigate to the date, book your time

---

## Pushover Setup

1. Download **Pushover** on your phone ($5 one-time, iOS/Android)
2. Sign up at [pushover.net](https://pushover.net) — your **User Key** is on the dashboard
3. Create an app token at [pushover.net/apps/build](https://pushover.net/apps/build)
4. Enter both keys in app Settings → click **📲 Test Notification**
5. In the Pushover app → Settings → **Open URLs in: Default Browser** (so links open in your logged-in browser)

Notifications are sent at **priority 1** (bypasses quiet hours).

---

## Finding Your Course Details

### Auto-detect (recommended)
Just paste the booking URL — the app fetches the page and scrapes the IDs automatically.

### Manual (if auto-detect fails)
1. Open your course's ForeUp booking URL in Chrome
2. Press **F12** → **Network** tab
3. Click a date in the booking calendar
4. Find the request to `/api/booking/times` — the URL contains `schedule_id=XXXX&booking_class=XXXX`

---

## How ForeUp Authentication Works

ForeUp uses session-based auth with a CSRF-style check:

1. **Visit booking page** → server issues a `PHPSESSID` session cookie
2. **POST login** with form-encoded credentials + session cookie
3. **Fetch tee times** with the active session

The app replicates this flow on every poll cycle using `requests.Session()`.

Confirmed working endpoints:

| Action | Method | Endpoint |
|---|---|---|
| Init session | `GET` | `/index.php/booking/{course_id}` |
| Login | `POST` | `/index.php/api/booking/users/login` |
| Fetch times | `GET` | `/index.php/api/booking/times` |

Required request headers (confirmed via Chrome DevTools):
- `Api-Key: no_limits`
- `X-Fu-Golfer-Location: foreup`
- `X-Requested-With: XMLHttpRequest`
- `Content-Type: application/x-www-form-urlencoded; charset=UTF-8` (login only)

Login payload fields (confirmed via DevTools):
- `username` (not `email`)
- `password`
- `booking_class_id`
- `course_id`
- `api_key`

Time format returned by API: `YYYY-MM-DD HH:MM` (e.g. `2026-03-20 15:20`)

> **Why doesn't the app book automatically?** ForeUp's booking confirmation step requires solving a reCAPTCHA, which can't be automated. The app finds the times and gets you to the booking page as fast as possible — you complete the last step yourself in a few taps.

---

## Database Schema

Three tables, auto-created on startup via `db.init_db()`:

```sql
config  (key TEXT PRIMARY KEY, value TEXT)

courses (course_id, schedule_id, booking_class, name, url, created_at)

jobs    (id, course_id, course_name, schedule_id, booking_class, course_url,
         target_date, time_from, time_to, players, holes, status,
         available_times JSONB, booked_confirmation JSONB,
         notification_sent, last_polled, created_at, logs JSONB)
```

Job status flow: `polling` → `available` → (manually booked on ForeUp)

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
| `POST` | `/api/resolve_course` | Auto-detect course IDs from URL |
| `GET` | `/api/courses` | List saved courses |
| `PUT` | `/api/courses/<id>` | Save/update a course manually |
| `DELETE` | `/api/courses/<id>` | Remove a saved course |
| `POST` | `/api/test_login` | Verify ForeUp credentials |
| `POST` | `/api/test_pushover` | Send a test notification |
| `GET` | `/api/scheduler_status` | Check polling thread health |
| `GET` | `/api/logs/<job_id>` | Get logs for a job |

---

## Pro Tips

> **ForeUp releases tee times exactly 7 days in advance at midnight (or 14 days for some courses — check the booking rules on the page).** Start your watch the evening before so it's running when slots drop.

- Run **multiple watches** simultaneously for different courses or dates
- Click **Logs** on any job card to see a timestamped poll history
- Visit `/api/scheduler_status` to verify the polling thread is alive after deploy
- Don't set `POLL_INTERVAL` below 60 — be respectful to the course's servers
- In Pushover app settings, set **Open URLs in: Default Browser** so booking links open in your logged-in browser

---

## Troubleshooting

**App crashes on startup (`DATABASE_URL` error)**
Add the PostgreSQL plugin in Railway and link `DATABASE_URL` to your web service under Variables.

**Login failing (401)**
Double-check `FOREUP_EMAIL` and `FOREUP_PASSWORD` in Railway Variables. No quotes, no spaces around values.

**Login failing ("Refresh required")**
The app visits the booking page first to get a session cookie before logging in. Make sure you're on v14+.

**Polling shows no times even though they exist on the site**
Check the job's time window — ForeUp returns times in `YYYY-MM-DD HH:MM` format. Make sure your time window covers the available slots (use 24h format, e.g. `15:00` for 3pm).

**Start Watching button does nothing**
Open browser DevTools (F12) → Console tab and check for red errors.

**Notification link opens ForeUp but resets to today's date**
This is a ForeUp frontend limitation — their single-page app ignores URL date params. Use the times listed in the notification body to find your slot manually after navigating to the correct date.

**No times showing after notification fires**
Someone else may have grabbed them before you got there. The job returns to polling automatically and will notify you again if new slots open.
