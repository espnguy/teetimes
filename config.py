"""Simple JSON config store for user credentials and preferences."""

import json
import os

CONFIG_FILE = os.environ.get("CONFIG_FILE", "config.json")

DEFAULTS = {
    "email": "",
    "password": "",
    "default_players": "2",
    "default_holes": "18",
    "poll_interval": "120",
    "notify_email": "",
    "pushover_user_token": "",
    "pushover_app_token": "",
    "dashboard_url": "",     # e.g. https://your-app.railway.app
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return dict(DEFAULTS)
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return {**DEFAULTS, **data}
    except Exception:
        return dict(DEFAULTS)


def save_config(data: dict):
    existing = load_config()
    existing.update(data)
    # Never store blank values over existing ones
    cleaned = {k: v for k, v in existing.items() if v != ""}
    with open(CONFIG_FILE, "w") as f:
        json.dump(cleaned, f, indent=2)
