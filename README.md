# ⛳ ForeUp Tee Time Auto-Booker

A self-hosted web dashboard that monitors ForeUp golf booking pages and alerts
you the instant a tee time opens up — then lets you confirm and book with one click.

---

## Features

- **Polls ForeUp every 2 minutes** (configurable) for your target date + time window
- **Web dashboard** accessible from any device — phone, tablet, laptop
- **One-click booking** — see all available times, pick one, confirm
- **Persistent jobs** — survives server restarts
- **Supports any ForeUp course** — just paste the booking URL
- **Multiple simultaneous watches** — different courses or dates at once

---

## Quick Start (Local)

```bash
# 1. Clone / unzip this folder
cd teetime-booker

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python app.py
```

Open **http://localhost:5000** in your browser.

---

## Deploy to the Cloud (Recommended)

### Option A — Railway (easiest, free tier available)

1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo — Railway auto-detects the `Procfile`
4. Set environment variables (see below) in the Railway dashboard
5. Done! Railway gives you a public URL

### Option B — Render

1. Push to GitHub
2. Go to [render.com](https://render.com) → New Web Service
3. Connect repo, set Build Command: `pip install -r requirements.txt`
4. Set Start Command: `gunicorn app:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT`
5. Add environment variables

### Option C — DigitalOcean App Platform / any VPS

```bash
# On your server
git clone <your-repo> && cd teetime-booker
pip install -r requirements.txt
gunicorn app:app --workers 1 --threads 4 --bind 0.0.0.0:5000 --daemon
```

---

## Environment Variables

| Variable        | Default            | Description                          |
|-----------------|--------------------|--------------------------------------|
| `PORT`          | `5000`             | HTTP port                            |
| `POLL_INTERVAL` | `120`              | Seconds between polls                |
| `STATE_FILE`    | `jobs_state.json`  | Where jobs are persisted             |
| `CONFIG_FILE`   | `config.json`      | Where credentials are stored         |

---

## How to Use

1. **Open the dashboard** at your server's URL
2. **Settings** — enter your ForeUp email + password and click Save
3. **Test Login** — confirm your credentials work
4. **Add a Watch:**
   - Paste your course's ForeUp booking URL
     (e.g. `https://foreupsoftware.com/index.php/booking/19536`)
   - Pick your target date
   - Set your preferred time window (e.g. 7:00am – 10:00am)
   - Choose number of players
5. **Wait** — when times open up the job card turns green
6. **Click "View & Confirm"** — see all available slots, select one, click Book

---

## Finding Your Course URL

1. Go to your golf course website
2. Click "Book a Tee Time" — you'll be redirected to a ForeUp page
3. Copy the URL. It will look like:
   `https://foreupsoftware.com/index.php/booking/NNNNN`
   where `NNNNN` is the course's numeric ID

---

## How ForeUp Booking Works (Technical)

ForeUp doesn't publish a public API, but its booking widget makes standard
JSON calls. This tool replicates those calls:

- `GET /api/booking/{course_id}/times?date=MM-DD-YYYY&...` — fetch available times
- `POST /api/booking/{course_id}/reserve` — book a time slot

Authentication uses a session cookie obtained by posting to
`/index.php/user/login`.

> **Note:** ForeUp may update their API. If polling starts failing, open
> browser DevTools → Network tab on the booking page to inspect the current
> endpoint structure, and update `foreup_client.py` accordingly.

---

## Important Notes

- This tool uses **your own ForeUp account** — you're booking as yourself
- ForeUp may impose booking windows (e.g. you can only book 7 days ahead)
- Set up a watch **the day before** times become available so you're ready
  the moment slots open
- Be respectful: don't set extremely aggressive poll intervals (< 60s)

---

## Files

```
teetime-booker/
├── app.py              ← Flask web app + API endpoints
├── foreup_client.py    ← ForeUp HTTP client (login, fetch, book)
├── scheduler.py        ← Background polling engine
├── config.py           ← Credentials & settings store
├── templates/
│   └── index.html      ← Web dashboard UI
├── requirements.txt
├── Procfile            ← For Railway/Render/Heroku
└── README.md
```
