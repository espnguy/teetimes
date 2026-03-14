# ⛳ ForeUp Tee Time Auto-Booker

> A self-hosted web dashboard that watches ForeUp golf courses 24/7 and lets you confirm and book the moment a tee time opens up.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?style=flat&logo=flask)
![Deploy on Railway](https://img.shields.io/badge/Deploy-Railway-8B5CF6?style=flat&logo=railway)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)

---

## What it does

1. You paste a ForeUp booking URL and set a date + preferred time window
2. The server polls that course every 2 minutes (configurable)
3. The moment a tee time appears in your window, the dashboard turns **green**
4. You open the dashboard, see all available slots, pick one, and click **Book**
5. Done — the reservation is made under your own ForeUp account

---

## Screenshots

| Dashboard | Confirm modal |
|-----------|--------------|
| Watches listed with live status | Pick a slot and confirm with one click |

---

## Features

- 🔄 **Automatic polling** — checks every 2 minutes while you do other things
- 📱 **Web dashboard** — access from your phone, tablet, or laptop
- ✅ **You confirm before booking** — no accidental reservations
- 💾 **Persistent jobs** — survives server restarts, watches stay active
- 🏌️ **Multi-course support** — watch different courses or dates simultaneously
- 🔑 **Your own account** — books as you, using your ForeUp credentials

---

## Quick Start

### Run locally

```bash
# 1. Unzip and enter the folder
cd teetime-booker

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the server
python app.py
```

Open **http://localhost:5000** in your browser.

---

## Deploy to the Cloud

Running this locally means your computer has to stay on. Deploying to a cloud server means it runs 24/7 automatically.

### ⭐ Option 1 — Railway (Recommended, ~$0–5/month)

Railway gives you **$5 free credit per month** — more than enough for this app.

```bash
# Install the Railway CLI
npm install -g @railway/cli

# From the teetime-booker folder:
railway login
railway init
railway up
```

Or connect via GitHub:
1. Push this folder to a private GitHub repo
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
3. Select your repo — Railway detects the `Procfile` automatically
4. Set your environment variables in the Railway dashboard (see below)
5. Railway gives you a public `https://` URL — open it from anywhere

### Option 2 — Fly.io (Free tier)

Fly.io includes 3 free shared VMs — this app fits easily.

```bash
# Install flyctl from fly.io/docs/hands-on/install-flyctl
fly launch
fly deploy
```

### Option 3 — Render ($7/month)

Render's free tier spins down after inactivity, which would stop polling.
Use their **Starter** plan ($7/month) for an always-on service.

1. Push to GitHub
2. Go to [render.com](https://render.com) → **New Web Service**
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `gunicorn app:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT`

---

## Environment Variables

Set these in your cloud dashboard (Railway, Fly.io, etc.) rather than storing credentials in files.

| Variable        | Default            | Description                              |
|-----------------|--------------------|------------------------------------------|
| `PORT`          | `5000`             | HTTP port the server listens on          |
| `POLL_INTERVAL` | `120`              | Seconds between polls (minimum: 60)      |
| `STATE_FILE`    | `jobs_state.json`  | Path where active jobs are saved         |
| `CONFIG_FILE`   | `config.json`      | Path where credentials are stored        |

---

## How to Use

### Step 1 — Configure credentials
Open the dashboard, click **⚙️ Settings**, enter your ForeUp email and password, and click **Save**. Use **Test Login** to confirm they work.

### Step 2 — Find your course URL
1. Go to your golf course website and click "Book a Tee Time"
2. You'll be redirected to a ForeUp page — copy that URL
3. It will look like: `https://foreupsoftware.com/index.php/booking/19536`

### Step 3 — Add a watch
- Paste the course URL
- Select your target date
- Set your earliest and latest acceptable tee time (e.g. 7:00 AM – 10:00 AM)
- Choose number of players
- Click **Start Watching**

### Step 4 — Wait for availability
The job card will show ⏳ **Polling** while checking. When a time opens up it flips to 🟢 **AVAILABLE**.

### Step 5 — Confirm and book
Click **View & Confirm**, select your preferred time slot from the list, and click **Book Selected Time**. The reservation is made immediately.

---

## Pro Tips

> **ForeUp typically releases tee times exactly 7 days in advance at midnight.**
> Start your watch the evening before so it's running the moment slots drop.

- You can run **multiple watches** simultaneously for different courses or dates
- Check the **Logs** button on any job to see a timestamped history of polls
- If a slot gets grabbed by someone else before you confirm, the job automatically returns to **Polling** status
- Don't set `POLL_INTERVAL` below 60 seconds — be respectful to the course's servers

---

## Project Structure

```
teetime-booker/
├── app.py               ← Flask web app + REST API endpoints
├── foreup_client.py     ← ForeUp HTTP client (login, fetch, book)
├── scheduler.py         ← Background polling engine (daemon thread)
├── config.py            ← Credentials and settings store
├── templates/
│   └── index.html       ← Full web dashboard (single-file, no build step)
├── requirements.txt     ← Python dependencies
├── Procfile             ← Start command for Railway / Render / Heroku
└── README.md
```

---

## How It Works (Technical)

ForeUp doesn't publish a public API, but their booking widget makes standard JSON calls that can be replicated with an authenticated HTTP session.

| Action | Endpoint |
|--------|----------|
| Login | `POST /index.php/user/login` |
| Fetch available times | `GET /api/booking/{course_id}/times?date=MM-DD-YYYY&players=N&...` |
| Book a reservation | `POST /api/booking/{course_id}/reserve` |

Authentication returns a session cookie which is included in all subsequent requests.

> **Note:** If polling starts returning errors, ForeUp may have updated their API.
> Open browser DevTools → Network tab on the booking page to inspect current
> endpoint structure, then update `foreup_client.py` accordingly.

---

## Important Notes

- This tool books using **your own ForeUp account** — it acts as you
- ForeUp enforces booking windows (e.g. 7 days in advance) — the tool respects these
- You remain in control: **nothing is booked without your confirmation**
- This is a personal automation tool — don't use it to hold times you don't intend to play

---

## Troubleshooting

**Login failing?**
Double-check your credentials on the ForeUp site directly. Some courses use a separate login system layered on top of ForeUp.

**No times showing in my window?**
The course may not have released times yet for that date, or all slots in your window are taken. Check the raw booking page to confirm.

**Booking fails after selecting a time?**
The slot may have been taken between the last poll and your confirmation — this is a race condition. The job will go back to polling and notify you when another opens.

**Deployed to cloud but jobs stop after a while?**
Make sure you're using a paid tier (not Render's free tier). Free tiers that spin down will kill the background polling thread.
