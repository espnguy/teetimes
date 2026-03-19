# ⛳ Tee Time Auto-Watcher

> A self-hosted web dashboard that watches golf courses 24/7 and sends you an instant Pushover notification with all available times the moment they open up.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?style=flat&logo=flask)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Railway-336791?style=flat&logo=postgresql)
![Deploy on Railway](https://img.shields.io/badge/Deploy-Railway-8B5CF6?style=flat&logo=railway)

---

## Supported Booking Platforms

| Platform | Example URL |
|---|---|
| **ForeUp** | `https://foreupsoftware.com/index.php/booking/19536` |
| **GolfNow** | `https://www.golfnow.com/tee-times/facility/12345-course-name` |
| **TeeItUp (.golf)** | `https://course-name.book.teeitup.golf/tee-times?facilityId=12345` |
| **TeeItUp (.com)** | `https://course-name.book.teeitup.com/?course=1307` |
| **Course website** | `https://www.pecanhollowgc.com/book-a-tee-time/` (auto-detects embedded platform) |

GolfNow and TeeItUp courses don't require login credentials — their tee time APIs are public. ForeUp requires your ForeUp account credentials.

---

## How it works

1. Paste a booking URL (or pick a saved course) and set your target date and time window
2. The server polls for available times every 2 minutes
3. The moment times appear in your window, you get a **Pushover push notification** listing every available slot with times, prices, and open spots
4. Tap **"Book on [Platform] →"** in the notification or dashboard to open the booking page and reserve manually
5. The job keeps polling and re-notifying if more times appear, and auto-expires once the date passes

> **Why no auto-booking?** Most platforms require solving a reCAPTCHA to complete a reservation. The app finds the times and gets you there as fast as possible — you complete the last step yourself in a few taps.

---

## Features

- 🔄 **Polls every 2 minutes** while you do other things
- 📲 **Pushover notifications** — instant phone alert listing all available times with prices
- 🏌️ **One-tap to book** — notification and dashboard both link directly to the booking page
- ⌛ **Auto-expiry** — jobs automatically stop polling once the target date passes
- 💾 **Persists across restarts** — jobs, courses, and settings stored in PostgreSQL
- 🏌️ **Saved course library** — set up a course once, reuse with one click
- 🔍 **Auto-detects course IDs** — paste any booking URL and IDs are scraped automatically
- 🌐 **Multi-platform** — ForeUp, GolfNow, and TeeItUp all supported

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

[Open Booking Page →]
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
├── golfnow_client.py    ← GolfNow/TeeItUp HTTP client (no auth required)
├── course_resolver.py   ← Auto-detects platform and IDs from any booking URL
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

| Variable | Value | Required for |
|---|---|---|
| `FOREUP_EMAIL` | your ForeUp login email | ForeUp courses only |
| `FOREUP_PASSWORD` | your ForeUp password | ForeUp courses only |
| `PUSHOVER_USER_TOKEN` | your Pushover user key | All notifications |
| `PUSHOVER_APP_TOKEN` | your Pushover app token | All notifications |
| `POLL_INTERVAL` | `120` (seconds, default 2 min) | Optional |

> ForeUp credentials are only used for ForeUp courses. GolfNow and TeeItUp courses work without any credentials.

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
export FOREUP_EMAIL="you@email.com"      # Only needed for ForeUp courses
export FOREUP_PASSWORD="yourpassword"    # Only needed for ForeUp courses

python app.py
```

Open **http://localhost:5000**

---

## Using the Dashboard

### First time setup
1. Open Settings → enter your Pushover keys → **Save**
2. Click **📲 Test Notification** to confirm Pushover is working
3. If using ForeUp courses: click **🔑 Test Login** to verify your credentials work

### Adding a watch
1. Paste any booking URL — ForeUp, GolfNow, TeeItUp, or even your course's own website
2. The app auto-detects the platform and scrapes the required IDs
3. If auto-detect fails, a yellow box appears with guidance on finding the IDs manually
4. Pick your target date and preferred time window (24h format, e.g. `07:00` to `16:00`)
5. Choose number of players → **Start Watching**

The course saves to your library automatically — next time just pick it from the dropdown.

### When times are found
- Job card turns 🟢 **AVAILABLE**
- You get a Pushover notification listing all available times with prices and spots
- Tap **"Open Booking Page →"** in the notification, or click **"🏌️ Book on [Platform] →"** on the dashboard
- Complete the booking on the platform's site (takes about 15 seconds once you're there)

### Job lifecycle
- **Polling** → actively checking every 2 minutes
- **Available** → times found, notification sent, waiting for you to book
- **Expired** → target date has passed, polling stopped automatically

Delete old jobs from the dashboard after you've booked.

---

## Pushover Setup

1. Download **Pushover** on your phone ($5 one-time, iOS/Android)
2. Sign up at [pushover.net](https://pushover.net) — your **User Key** is on the dashboard
3. Create an app token at [pushover.net/apps/build](https://pushover.net/apps/build)
4. Enter both keys in app Settings → click **📲 Test Notification**
5. In the Pushover app → Settings → **Open URLs in: Default Browser** so booking links open in your logged-in browser

Notifications are sent at **priority 1** (bypasses quiet hours).

---

## Finding Course IDs Manually

### Auto-detect (recommended)
Paste any URL — the app fetches the page and extracts IDs automatically. Even course website URLs like `https://www.pecanhollowgc.com/book-a-tee-time/` work — the app follows embedded booking widget links.

### ForeUp (manual fallback)
1. Open the booking URL in Chrome → F12 → Network tab
2. Click a date in the calendar
3. Find the request to `/api/booking/times` — URL contains `schedule_id=XXXX` and `booking_class=XXXX`

### GolfNow (manual fallback)
The facility ID is in the URL path: `golfnow.com/tee-times/facility/`**`12345`**`-course-name`

### TeeItUp (manual fallback)
Look for `facilityId`, `courseId`, or `course` in the booking URL query string.

---

## How Authentication Works

### ForeUp
ForeUp uses session-based auth. The app replicates the browser login flow on every poll:

1. GET the booking page → receives a `PHPSESSID` session cookie
2. POST login with form-encoded credentials + cookie
3. GET tee times with the active session

Confirmed login payload fields (from DevTools):
- `username` (not `email`)
- `password`
- `booking_class_id`
- `course_id`
- `api_key: no_limits`

Required request headers:
- `Api-Key: no_limits`
- `X-Fu-Golfer-Location: foreup`
- `X-Requested-With: XMLHttpRequest`

Time format returned: `YYYY-MM-DD HH:MM` (e.g. `2026-03-20 15:20`)

### GolfNow / TeeItUp
No authentication required. The tee times API is public. The app queries the API directly with the facility ID and returns available slots.

Supported URL formats:
- `https://www.golfnow.com/tee-times/facility/12345-course-name`
- `https://course-name.book.teeitup.golf/tee-times?facilityId=12345`
- `https://course-name.book.teeitup.com/?course=1307`

---

## Database Schema

Three tables, auto-created on startup:

```sql
config  (key TEXT PRIMARY KEY, value TEXT)

courses (course_id, schedule_id, booking_class, name, url, platform, created_at)

jobs    (id, course_id, course_name, schedule_id, booking_class, course_url,
         target_date, time_from, time_to, players, holes, status, platform,
         available_times JSONB, booked_confirmation JSONB,
         notification_sent, last_polled, created_at, logs JSONB)
```

Job status flow: `polling` → `available` → `expired` (auto) or deleted manually after booking

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
| `POST` | `/api/resolve_course` | Auto-detect platform and IDs from URL |
| `GET` | `/api/courses` | List saved courses |
| `PUT` | `/api/courses/<id>` | Save/update a course manually |
| `DELETE` | `/api/courses/<id>` | Remove a saved course |
| `POST` | `/api/test_login` | Verify ForeUp credentials |
| `POST` | `/api/test_pushover` | Send a test notification |
| `GET` | `/api/scheduler_status` | Check polling thread health |
| `GET` | `/api/logs/<job_id>` | Get logs for a job |

---

## Pro Tips

> **Tee times typically release 7–14 days in advance.** Check the booking rules on your course's page. Start your watch the evening before so it's running when slots drop.

- Run **multiple watches** simultaneously for different courses or dates
- Click **Logs** on any job card to see a timestamped poll history
- In Pushover app settings, set **Open URLs in: Default Browser** so links open in your logged-in browser
- Visit `/api/scheduler_status` to verify the polling thread is alive after deploy
- Don't set `POLL_INTERVAL` below 60 — be respectful to the course's servers

---

## Troubleshooting

**App crashes on startup**
Add the PostgreSQL plugin in Railway and link `DATABASE_URL` to your web service under Variables.

**ForeUp login failing (401)**
Double-check `FOREUP_EMAIL` and `FOREUP_PASSWORD` in Railway Variables — no quotes, no spaces.

**"Could not extract facility ID from URL"**
The URL format isn't recognized. Try pasting the direct booking page URL instead of the course's main website. See the supported URL formats above.

**Polling shows no times even though they exist on the site**
Check your time window covers the available slots (use 24h format — `15:00` for 3pm). Also check the Logs on the job card to see exactly what the last poll returned.

**Notification link opens the booking page but resets to today's date**
This is a frontend limitation on most booking platforms — their single-page apps ignore URL date parameters. Use the times listed in the notification body to find your slot after navigating to the correct date.

**No times showing after notification fires**
Someone else may have grabbed them. The job returns to polling automatically and will notify you again if new slots open.

**Start Watching button does nothing**
Open browser DevTools (F12) → Console tab and check for red errors.
