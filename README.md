# ⛳ Tee Time Auto-Watcher

> A self-hosted web dashboard that watches golf courses 24/7 and sends you an instant Pushover notification with all available times the moment they open up.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?style=flat&logo=flask)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Railway-336791?style=flat&logo=postgresql)
![Deploy on Railway](https://img.shields.io/badge/Deploy-Railway-8B5CF6?style=flat&logo=railway)

---

## Supported Booking Platforms

| Platform | Status | Example URL |
|---|---|---|
| **ForeUp** | ✅ Confirmed working | `https://foreupsoftware.com/index.php/booking/19536` |
| **GolfNow** | ✅ Confirmed working | `https://www.golfnow.com/tee-times/facility/1307-pecan-hollow-golf-course` |
| **TeeItUp (.golf)** | ⚠️ Requires manual ObjectId | `https://course-name.book.teeitup.golf/tee-times` |
| **TeeItUp (.com)** | ⚠️ Requires manual ObjectId | `https://course-name.book.teeitup.com/?course=1307` |
| **Course website** | ✅ Auto-detects embedded platform | `https://www.pecanhollowgc.com/book-a-tee-time/` |

**ForeUp** requires your ForeUp account credentials. **GolfNow** and **TeeItUp** require no login.

> **TeeItUp note:** TeeItUp is powered by the Kenna/Lightspeed Golf backend. To add a TeeItUp course, paste the URL — when auto-detect fails, open DevTools on the booking page, pick a date, find the request to `phx-api-be-east-1b.kenna.io/course/{objectId}/tee-time/locks`, and copy the 24-char hex ObjectId into the Kenna ObjectId field.

---

## How it works

1. Paste a booking URL (or pick a saved course) and set your target date and time window
2. The server polls for available times every 2 minutes
3. The moment times appear in your window, you get a **Pushover push notification** listing every available slot with times and prices
4. Tap **"Book on [Platform] →"** in the notification or dashboard to open the booking page and reserve manually
5. Jobs auto-expire once the target date passes

> **Why no auto-booking?** Most platforms require solving a reCAPTCHA to complete a reservation. The app finds the times and gets you there as fast as possible — you complete the last step yourself in a few taps.

---

## Features

- 🔄 **Polls every 2 minutes** while you do other things
- 📲 **Pushover notifications** — instant alert listing all available times with prices
- 🏌️ **One-tap to book** — notification and dashboard both link directly to the booking page
- ⌛ **Auto-expiry** — jobs stop polling automatically once the target date passes
- 💾 **Persists across restarts** — jobs, courses, and settings stored in PostgreSQL
- 🏌️ **Saved course library** — set up a course once, reuse with one click
- 🔍 **Auto-detects course IDs** — paste any booking URL and IDs are scraped automatically
- 🌐 **Multi-platform** — ForeUp and GolfNow confirmed, TeeItUp with manual setup

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
├── db.py                ← PostgreSQL data layer
├── scheduler.py         ← Background polling thread (starts at module load)
├── foreup_client.py     ← ForeUp HTTP client (session init, login, fetch times)
├── golfnow_client.py    ← GolfNow/TeeItUp HTTP client (no auth required)
├── course_resolver.py   ← Auto-detects platform and IDs from any booking URL
├── notifier.py          ← Pushover push notifications
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

> ⚠️ This step is required. The app crashes on startup without `DATABASE_URL`.

### 4. Set environment variables

In your web service → **Variables** tab:

| Variable | Value | Required for |
|---|---|---|
| `FOREUP_EMAIL` | your ForeUp login email | ForeUp courses only |
| `FOREUP_PASSWORD` | your ForeUp password | ForeUp courses only |
| `PUSHOVER_USER_TOKEN` | your Pushover user key | All notifications |
| `PUSHOVER_APP_TOKEN` | your Pushover app token | All notifications |
| `POLL_INTERVAL` | `120` (seconds, default 2 min) | Optional |

### 5. Deploy

Railway auto-deploys on every push to `main`.

---

## Quick Start (Local)

```bash
cd teetime-booker
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql://..."
export FOREUP_EMAIL="you@email.com"      # ForeUp courses only
export FOREUP_PASSWORD="yourpassword"    # ForeUp courses only

python app.py
```

Open **http://localhost:5000**

---

## Using the Dashboard

### First time setup
1. Open Settings → enter your Pushover keys → **Save**
2. Click **📲 Test Notification** to confirm Pushover works
3. If using ForeUp: click **🔑 Test Login** to verify credentials

### Adding a watch
1. Paste any booking URL — ForeUp, GolfNow, TeeItUp, or your course's own website
2. The app auto-detects the platform and scrapes required IDs
3. If auto-detect fails, a yellow box appears — fill in IDs manually (see below)
4. Pick your target date and time window (24h format, e.g. `07:00` to `16:00`)
5. Choose number of players → **Start Watching**

Courses save to your library automatically — next time pick from the dropdown.

### When times are found
- Job turns 🟢 **AVAILABLE**
- Pushover notification lists all available times with prices and spots
- Tap **"Open Booking Page →"** or click **"🏌️ Book on [Platform] →"** on the dashboard
- Complete booking on the platform's site (~15 seconds)

### Job lifecycle
- **Polling** → checking every 2 minutes
- **Available** → times found, notification sent
- **Expired** → target date passed, polling stopped automatically

---

## Pushover Setup

1. Download **Pushover** ($5 one-time, iOS/Android)
2. Sign up at [pushover.net](https://pushover.net) — **User Key** is on your dashboard
3. Create an app token at [pushover.net/apps/build](https://pushover.net/apps/build)
4. Enter both in app Settings → **📲 Test Notification**
5. In Pushover app → Settings → **Open URLs in: Default Browser**

Notifications fire at **priority 1** (bypasses quiet hours).

---

## Finding Course IDs Manually

### Auto-detect (recommended)
Paste any URL — even a course website like `https://www.pecanhollowgc.com/book-a-tee-time/`. The app fetches the page and follows embedded booking links automatically.

### ForeUp
1. Open the booking URL in Chrome → F12 → Network
2. Click a date in the calendar
3. Find `/api/booking/times` request — URL contains `schedule_id=XXXX` and `booking_class=XXXX`

### GolfNow
Facility ID is in the URL path: `golfnow.com/tee-times/facility/`**`1307`**`-course-name`

### TeeItUp (Kenna)
1. Open the booking page → F12 → Network → pick a date
2. Find the request to `phx-api-be-east-1b.kenna.io/course/{objectId}/tee-time/locks`
3. Copy the 24-char hex ObjectId from the URL path
4. Paste into the **Kenna ObjectId** field in the manual override section

---

## How Authentication Works

### ForeUp
Session-based auth — the app replicates the browser login flow on every poll:
1. GET booking page → receives `PHPSESSID` cookie
2. POST login with credentials + cookie
3. GET tee times with active session

Confirmed login payload fields: `username`, `password`, `booking_class_id`, `course_id`, `api_key: no_limits`

Required headers: `Api-Key: no_limits`, `X-Fu-Golfer-Location: foreup`, `X-Requested-With: XMLHttpRequest`

### GolfNow
POST to `https://www.golfnow.com/api/tee-times/tee-time-search-results` with a JSON body containing `facilityId`, formatted date, and search parameters. No auth required — the endpoint is public.

Response structure: `ttResults.teeTimes[].{time.date, teeTimeRates[].{holeCount, singlePlayerPrice}}`

### TeeItUp (Kenna)
GET to `https://phx-api-be-east-1b.kenna.io/course/{objectId}/tee-time/locks?localDate=YYYY-MM-DD` with `X-Be-Alias: {subdomain-slug}` header. No auth required.

---

## Database Schema

```sql
config  (key TEXT PRIMARY KEY, value TEXT)

courses (course_id, schedule_id, booking_class, name, url, platform, be_alias, created_at)

jobs    (id, course_id, course_name, schedule_id, booking_class, course_url,
         target_date, time_from, time_to, players, holes, status,
         platform, be_alias, available_times JSONB, booked_confirmation JSONB,
         notification_sent, last_polled, created_at, logs JSONB)
```

Job status: `polling` → `available` → `expired` (auto) or deleted manually after booking.

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Dashboard UI |
| `POST` | `/config` | Save settings |
| `POST` | `/api/add_job` | Start a new watch |
| `DELETE` | `/api/remove_job/<id>` | Remove a watch |
| `GET` | `/api/jobs` | List all jobs |
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
- In Pushover → Settings → **Open URLs in: Default Browser** so links open in your logged-in browser
- Don't set `POLL_INTERVAL` below 60 — be respectful to course servers

---

## Troubleshooting

**App crashes on startup**
Link `DATABASE_URL` from the Railway Postgres plugin to your web service under Variables.

**ForeUp login failing (401)**
Check `FOREUP_EMAIL` and `FOREUP_PASSWORD` in Railway Variables — no quotes, no spaces.

**"Could not extract facility ID from URL"**
Try the direct booking URL rather than the course website. For TeeItUp, use the Kenna ObjectId override.

**Polling returns 0 times even though they exist**
Check your time window is in 24h format (e.g. `15:00` for 3pm). Check the job Logs for what the last poll returned.

**GolfNow returns $0 green fees**
Normal — GolfNow hides pricing for users who aren't logged in. Times will still appear correctly.

**Notification link resets to today's date**
Most booking platforms ignore URL date params on their SPAs. Use the times in the notification body to find your slot after navigating to the correct date.

**No times after notification fires**
Someone else grabbed them. The job returns to polling and will re-notify if new slots open.
