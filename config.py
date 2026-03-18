"""
config.py — Persistent settings for the Literary Clock.

Settings are stored in config.json next to this file and loaded at startup.
Unknown keys in config.json are ignored; missing keys fall back to DEFAULTS.

To add a new persistent setting:
  1. Add it to DEFAULTS with its default value and a comment.
  2. Use config.get("your_key") anywhere to read it.
  3. Use config.set_val("your_key", value) to write + save.
  4. Use config.toggle("your_key") for boolean flip + save.
"""

import json
import os

_DIR  = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(_DIR, "config.json")

# ── All settings and their defaults ───────────────────────────────────────────
DEFAULTS: dict = {

    # ── Clock display ─────────────────────────────────────────────────────────
    "time_24h":             True,   # True = 24 h clock, False = 12 h with AM/PM
    "show_wifi_indicator":  True,   # show WiFi icon on clock face

    # ── Font sizes (px) ───────────────────────────────────────────────────────
    "font_size_quote":      38,     # main quote text (starting/maximum size)
    "font_size_quote_min":  18,     # minimum size before giving up shrinking

    # ── Quote update interval ────────────────────────────────────────────────
    # How often the quote changes. Must be 1, 5, or 10.
    # 1  = unique quote every minute (uses full pool for gaps)
    # 5  = quote changes every 5 minutes (classic literary clock behaviour)
    # 10 = quote changes every 10 minutes
    "quote_interval":       1,
    "font_size_time":       30,     # HH:MM on clock face
    "font_size_meta":       22,     # "Work — Author" attribution
    "font_size_menu_title": 34,     # menu header
    "font_size_menu_item":  30,     # menu list items
    "font_size_menu_hint":  20,     # footer button hints
    "font_size_settime":    96,     # big digits on time-set screen

    # ── E-ink refresh ─────────────────────────────────────────────────────────
    "full_refresh_every":   8,      # full ghost-clearing refresh every N updates

    # ── Buttons — BCM GPIO pin numbers (set to null to disable) ──────────────
    # Wire each button between the GPIO pin and GND.
    # Internal pull-ups are enabled — no external resistors needed.
    #
    #  btn_menu   : opens / closes the menu (or cancels time-set)
    #  btn_up     : navigate up / increment value
    #  btn_down   : navigate down / decrement value
    #  btn_select : confirm / select highlighted item
    #
    "btn_menu":             5,
    "btn_up":               6,
    "btn_down":             13,
    "btn_select":           19,

    # ── Time offset ───────────────────────────────────────────────────────────
    # Seconds added to system time for display. Adjusted by "Set Time Manually".
    # "Sync via NTP" resets this to 0. No sudo required — system clock is untouched.
    "time_offset_seconds":  0,
}

_data: dict = {}


def load() -> dict:
    """Load settings from disk, merging with DEFAULTS for any missing keys."""
    global _data
    _data = dict(DEFAULTS)
    try:
        with open(_PATH) as f:
            _data.update(json.load(f))
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as e:
        print(f"config: could not read {_PATH}: {e} — using defaults")
    return _data


def save() -> None:
    """Write current settings to disk."""
    try:
        with open(_PATH, "w") as f:
            json.dump(_data, f, indent=2)
    except OSError as e:
        print(f"config: save failed: {e}")


def get(key: str):
    """Return the current value of a setting (falls back to DEFAULTS)."""
    return _data.get(key, DEFAULTS.get(key))


def set_val(key: str, value) -> None:
    """Set a value and persist to disk immediately."""
    _data[key] = value
    save()


def toggle(key: str) -> bool:
    """Flip a boolean setting, persist, and return the new value."""
    new_val = not bool(get(key))
    set_val(key, new_val)
    return new_val