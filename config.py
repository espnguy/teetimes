"""
Config store - reads from environment variables first, then falls back to config.json.

On Railway (or any cloud host), set these environment variables in the dashboard:
  FOREUP_EMAIL
  FOREUP_PASSWORD
  PUSHOVER_USER_TOKEN
  PUSHOVER_APP_TOKEN
  DASHBOARD_URL

Any value set via the Settings UI in the app is saved to config.json and will
be used if the environment variable is not set. On Railway, env vars always win.
"""

import json
import os

CONFIG_FILE = os.environ.get("CONFIG_FILE", "config.json")

DEFAULTS = {
    "email": "",
    "password": "",
    "default_players": "2",
    "default_holes": "18",
    "poll_interval": "120",
    "pushover_user_token": "",
    "pushover_app_token": "",
    "dashboard_url": "",
}

# Map of env var name -> config key
ENV_MAP = {
    "FOREUP_EMAIL":          "email",
    "FOREUP_PASSWORD":       "password",
    "PUSHOVER_USER_TOKEN":   "pushover_user_token",
    "PUSHOVER_APP_TOKEN":    "pushover_app_token",
    "DASHBOARD_URL":         "dashboard_url",
    "POLL_INTERVAL":         "poll_interval",
    "DEFAULT_PLAYERS":       "default_players",
}


def load_config() -> dict:
    """Load config, with env vars taking priority over saved file values."""
    # Start with defaults
    cfg = dict(DEFAULTS)

    # Layer in anything saved to the JSON file
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            cfg.update({k: v for k, v in saved.items() if v})
        except Exception:
            pass

    # Env vars always win — overwrite whatever was in the file
    for env_key, cfg_key in ENV_MAP.items():
        val = os.environ.get(env_key, "").strip()
        if val:
            cfg[cfg_key] = val

    return cfg


def save_config(data: dict):
    """
    Save config to JSON file. Values that are already set via env vars are still
    saved here, but the env var will take priority on next load.
    """
    existing = load_config()
    existing.update({k: v for k, v in data.items() if v != ""})
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Could not save config: {e}")


def credentials_from_env() -> bool:
    """Return True if credentials are coming from environment variables."""
    return bool(os.environ.get("FOREUP_EMAIL") and os.environ.get("FOREUP_PASSWORD"))
