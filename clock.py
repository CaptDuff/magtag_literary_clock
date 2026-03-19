#!/usr/bin/env python3
"""
Literary Clock — Raspberry Pi + Waveshare 7.5" e-ink (800×480)

Starts in clock mode. Four buttons navigate a menu for time adjustment,
NTP sync, and display toggles.

Button wiring (BCM pins — configure in config.json):
  btn_menu   (default 5)  — open menu / cancel / back
  btn_up     (default 6)  — navigate up / increment
  btn_down   (default 13) — navigate down / decrement
  btn_select (default 19) — confirm / select

Wire each button between the GPIO pin and GND.
Internal pull-ups enabled — no resistors needed.

Systemd service: see literary-clock.service in this directory.
"""

import datetime
import os
import queue
import signal
import socket
import sys
import time

from PIL import Image, ImageDraw, ImageFont

import config
from menu import Menu, MenuItem, MsgResult

# ── Waveshare library ──────────────────────────────────────────────────────────
_LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

try:
    from waveshare_epd import epd7in5_V2
    EPD_AVAILABLE = True
except ImportError:
    EPD_AVAILABLE = False
    print("⚠  waveshare_epd not found — PREVIEW MODE (PNG → /tmp/literary_clock_preview.png)")

# ── Layout constants ───────────────────────────────────────────────────────────
WIDTH,  HEIGHT  = 800, 480
MARGIN          = 36
LINE_SPACING    = 10
HEADER_H        = 60    # menu / set-time header bar height
FOOTER_H        = 46    # menu / set-time footer bar height
MENU_ITEM_H     = 58    # height of each menu row

# ── Application states ─────────────────────────────────────────────────────────
STATE_CLOCK   = "clock"
STATE_MENU    = "menu"
STATE_SET_H   = "set_h"    # time-set screen, editing hours
STATE_SET_M   = "set_m"    # time-set screen, editing minutes
STATE_MESSAGE = "message"  # brief status screen (auto-exits after MESSAGE_TTL s)

MESSAGE_TTL   = 2.5        # seconds to show a status message

# ── CSV path ───────────────────────────────────────────────────────────────────
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quotes_merged.csv")





# ═══════════════════════════════════════════════════════════════════════════════
# FONT LOADING
# ═══════════════════════════════════════════════════════════════════════════════
# Candidates searched in order — first existing file wins.
# Drop a TTF into ./fonts/ to override system fonts.

_DIR = os.path.dirname(os.path.abspath(__file__))

_SERIF = [
    os.path.join(_DIR, "fonts", "serif.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
]
_SERIF_BOLD = [
    os.path.join(_DIR, "fonts", "serif-bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
]
_SERIF_ITALIC = [
    os.path.join(_DIR, "fonts", "serif-italic.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
]


def _find_font(candidates: list) -> str | None:
    for path in candidates:
        if os.path.isfile(path):
            return path
    import glob
    for pat in ["/usr/share/fonts/**/*.ttf", "/usr/share/fonts/**/*.otf"]:
        found = sorted(glob.glob(pat, recursive=True))
        if found:
            return found[0]
    return None


def _load_font(candidates: list, size: int) -> ImageFont.FreeTypeFont:
    path = _find_font(candidates)
    if path:
        try:
            font = ImageFont.truetype(path, size)
            print(f"  font: {os.path.basename(path)} @ {size}px")
            return font
        except (IOError, OSError):
            pass
    print(f"  ⚠ font not found — PIL bitmap fallback @ {size}px")
    return ImageFont.load_default()


def _reload_fonts() -> None:
    """Load (or reload) all fonts from config sizes. Called at startup."""
    global fnt_quote, fnt_bold, fnt_time, fnt_meta
    global fnt_menu_title, fnt_menu_item, fnt_menu_hint, fnt_settime

    fnt_quote      = _load_font(_SERIF,        config.get("font_size_quote"))
    fnt_bold       = _load_font(_SERIF_BOLD,   config.get("font_size_quote"))
    fnt_time       = _load_font(_SERIF_BOLD,   config.get("font_size_time"))
    fnt_meta       = _load_font(_SERIF_ITALIC, config.get("font_size_meta"))
    fnt_menu_title = _load_font(_SERIF_BOLD,   config.get("font_size_menu_title"))
    fnt_menu_item  = _load_font(_SERIF,        config.get("font_size_menu_item"))
    fnt_menu_hint  = _load_font(_SERIF_ITALIC, config.get("font_size_menu_hint"))
    fnt_settime    = _load_font(_SERIF_BOLD,   config.get("font_size_settime"))


# Initialised as None — _reload_fonts() sets them at startup
fnt_quote = fnt_bold = fnt_time = fnt_meta = None
fnt_menu_title = fnt_menu_item = fnt_menu_hint = fnt_settime = None


# ═══════════════════════════════════════════════════════════════════════════════
# QUOTE LOADING + TEXT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _load_quotes(path: str) -> dict:
    mapping: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip().strip('"')
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|", 4)
                if len(parts) < 4:
                    continue
                hhmm   = parts[0].strip().strip('"')
                quote  = parts[1].strip().strip('"')
                work   = parts[2].strip() if len(parts) > 2 else ""
                author = parts[3].strip() if len(parts) > 3 else ""
                tag    = parts[4].strip() if len(parts) > 4 else ""
                digits = hhmm.replace(":", "")
                if len(digits) == 3:
                    digits = "0" + digits
                if len(digits) == 4:
                    hhmm = f"{digits[:2]}:{digits[2:]}"
                if len(hhmm) == 5 and hhmm[2] == ":":
                    mapping.setdefault(hhmm, []).append((quote, work, author, tag))
    except (OSError, FileNotFoundError) as e:
        print(f"Could not load quotes: {e}")
    return mapping


QUOTES: dict = {}


def _init_quotes() -> None:
    global QUOTES
    QUOTES = _load_quotes(CSV_PATH)
    print(f"Loaded {sum(len(v) for v in QUOTES.values())} quotes "
          f"across {len(QUOTES)} minutes.")
    if not QUOTES:
        QUOTES = {"12:00": [("It is ^now^.", "Literary Clock", "System", "")]}


def pick_quote(h: int, m: int) -> tuple:
    """Return a quote for (h, m), respecting the quote_interval setting.

    interval=1:  unique quote every minute. Exact matches used first; gaps
                 filled from the full pool via a stable (h, m) hash.
    interval=5:  snap to the nearest 5-minute bucket (classic literary clock).
    interval=10: snap to the nearest 10-minute bucket.
    """
    interval = config.get("quote_interval") or 1

    # Snap to bucket for 5/10 min modes
    if interval in (5, 10):
        m = (m // interval) * interval

    key = f"{h:02d}:{m:02d}"
    lst = QUOTES.get(key)
    if lst:
        return lst[h % len(lst)]

    if interval == 1:
        # No exact match -- spread across the full pool, stable per (h, m)
        all_quotes = [q for quotes in QUOTES.values() for q in quotes]
        return all_quotes[(h * 60 + m) % len(all_quotes)]

    # 5/10 min mode: walk back within the bucket to find the nearest entry
    base = datetime.datetime(2000, 1, 1, h, m)
    for delta in range(1, interval + 1):
        prev = base - datetime.timedelta(minutes=delta)
        alt  = f"{prev.hour:02d}:{prev.minute:02d}"
        lst  = QUOTES.get(alt)
        if lst:
            return lst[h % len(lst)]

    # Last resort
    all_quotes = [q for quotes in QUOTES.values() for q in quotes]
    return all_quotes[(h * 60 + m) % len(all_quotes)]


def parse_spans(text: str) -> list:
    """Split 'some ^bold^ text' into [(str, is_bold), ...]."""
    parts = text.split("^")
    return [(part, i % 2 == 1) for i, part in enumerate(parts) if part]


def _wrap_spans(spans: list, draw: ImageDraw.ImageDraw, max_width: int,
                fnt_r: ImageFont.FreeTypeFont,
                fnt_b: ImageFont.FreeTypeFont) -> list:
    """Word-wrap (text, bold) spans into lines that fit max_width."""
    words = []
    for text, bold in spans:
        for w in text.split():
            if w:
                words.append((w, bold))

    lines, current, current_w = [], [], 0.0
    space_w = draw.textlength(" ", font=fnt_r)

    for word, bold in words:
        font   = fnt_b if bold else fnt_r
        word_w = draw.textlength(word, font=font)
        gap    = space_w if current else 0.0
        if current and (current_w + gap + word_w) > max_width:
            lines.append(current)
            current, current_w = [(word, bold)], word_w
        else:
            current.append((word, bold))
            current_w += gap + word_w
    if current:
        lines.append(current)
    return lines


def _draw_quote(draw: ImageDraw.ImageDraw, spans: list,
                x: int, y: int, max_width: int,
                fnt_r=None, fnt_b=None) -> int:
    """Draw word-wrapped quote. Returns y after the last line."""
    fnt_r = fnt_r or fnt_quote
    fnt_b = fnt_b or fnt_bold

    lines   = _wrap_spans(spans, draw, max_width, fnt_r, fnt_b)
    bbox    = draw.textbbox((0, 0), "Agjy", font=fnt_r)
    line_h  = bbox[3] - bbox[1] + LINE_SPACING
    space_w = int(draw.textlength(" ", font=fnt_r))

    for line in lines:
        cx = x
        for i, (word, bold) in enumerate(line):
            font = fnt_b if bold else fnt_r
            draw.text((cx, y), word, font=font, fill=0)
            cx += int(draw.textlength(word, font=font))
            if i < len(line) - 1:
                cx += space_w
        y += line_h
    return y


def _fit_quote(draw: ImageDraw.ImageDraw, spans: list,
               x: int, y: int, max_width: int, max_y: int):
    """
    Return (fnt_r, fnt_b) at the largest size where the quote fits within max_y.
    Steps down by 2px per attempt.

    Config keys:
      font_size_quote      — starting (maximum) size
      font_size_quote_min  — floor (default 18px)
    """
    start_size = config.get("font_size_quote")
    min_size   = config.get("font_size_quote_min") or 18
    step       = 2

    for size in range(start_size, min_size - 1, -step):
        fnt_r = _load_font(_SERIF,      size)
        fnt_b = _load_font(_SERIF_BOLD, size)
        lines  = _wrap_spans(spans, draw, max_width, fnt_r, fnt_b)
        bbox   = draw.textbbox((0, 0), "Agjy", font=fnt_r)
        line_h = bbox[3] - bbox[1] + LINE_SPACING
        if y + len(lines) * line_h <= max_y:
            if size < start_size:
                print(f"  quote font shrunk: {start_size}px -> {size}px")
            return fnt_r, fnt_b

    # Hit the floor — return minimum and let it clip rather than crash
    return _load_font(_SERIF, min_size), _load_font(_SERIF_BOLD, min_size)


def fmt_time(h: int, m: int) -> str:
    if config.get("time_24h"):
        return f"{h:02d}:{m:02d}"
    period = "AM" if h < 12 else "PM"
    return f"{h % 12 or 12}:{m:02d} {period}"


# ═══════════════════════════════════════════════════════════════════════════════
# BUTTON SETUP  (interrupt-driven — responsive even during display updates)
# ═══════════════════════════════════════════════════════════════════════════════

_btn_queue: queue.SimpleQueue = queue.SimpleQueue()
_gpio_ok = False


def _setup_buttons() -> None:
    global _gpio_ok
    pins = {
        "MENU":   config.get("btn_menu"),
        "UP":     config.get("btn_up"),
        "DOWN":   config.get("btn_down"),
        "SELECT": config.get("btn_select"),
    }
    active = {name: pin for name, pin in pins.items() if pin is not None}
    if not active:
        print("No buttons configured (all btn_* are null in config.json).")
        return
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        for name, pin in active.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                pin, GPIO.FALLING,
                callback=lambda ch, n=name: _btn_queue.put(n),
                bouncetime=300,
            )
        _gpio_ok = True
        print(f"Buttons registered: {active}")
    except ImportError:
        print("RPi.GPIO not available — buttons disabled.")
    except RuntimeError as e:
        print(f"GPIO setup error: {e}")


def _next_button() -> str | None:
    try:
        return _btn_queue.get_nowait()
    except queue.Empty:
        return None


def _setup_keyboard() -> None:
    """
    Feed keypresses into the same _btn_queue as GPIO buttons.
    Works alongside physical buttons — both active at the same time.
    Only runs when stdin is a real terminal (not when started by systemd).

    Key map:
      m / M        -> MENU
      w / W        -> UP
      s / S        -> DOWN
      Enter/Space  -> SELECT
      Ctrl+C       -> clean shutdown
    """
    import threading
    import termios
    import tty

    if not sys.stdin.isatty():
        return   # not a terminal (e.g. running as a systemd service)

    def _read_keys():
        fd  = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("m", "M"):
                    _btn_queue.put("MENU")
                elif ch in ("w", "W"):
                    _btn_queue.put("UP")
                elif ch in ("s", "S"):
                    _btn_queue.put("DOWN")
                elif ch in ("\r", "\n", " "):
                    _btn_queue.put("SELECT")
                elif ch == "\x03":          # Ctrl+C
                    os.kill(os.getpid(), signal.SIGINT)
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    t = threading.Thread(target=_read_keys, daemon=True)
    t.start()
    print("Keyboard: [m] menu  [w] up  [s] down  [Enter] select  [Ctrl+C] quit")


# ═══════════════════════════════════════════════════════════════════════════════
# NETWORK UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

_wifi_cache: tuple = (False, 0.0)
_WIFI_TTL = 30.0


def is_connected() -> bool:
    """Return True if internet is reachable (cached for _WIFI_TTL seconds)."""
    global _wifi_cache
    connected, ts = _wifi_cache
    if time.monotonic() - ts < _WIFI_TTL:
        return connected
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        connected = True
    except OSError:
        connected = False
    _wifi_cache = (connected, time.monotonic())
    return connected


def invalidate_wifi_cache() -> None:
    global _wifi_cache
    _wifi_cache = (False, 0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# TIME UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def get_display_time() -> tuple:
    """Return (hour, minute) adjusted by any stored manual offset."""
    offset = datetime.timedelta(seconds=config.get("time_offset_seconds") or 0)
    now    = datetime.datetime.now() + offset
    return now.hour, now.minute


def apply_manual_time(h: int, m: int) -> None:
    """
    Store a time offset so the display shows h:m right now.
    The system clock is NOT modified — no sudo required.
    """
    now    = datetime.datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    diff   = (target - now).total_seconds()
    if diff < -1800:
        target += datetime.timedelta(days=1)
    config.set_val("time_offset_seconds", int((target - now).total_seconds()))


def sync_ntp() -> None:
    """Reset manual time offset — trusts the Pi's NTP-synced system clock."""
    config.set_val("time_offset_seconds", 0)


# ═══════════════════════════════════════════════════════════════════════════════
# DISPLAY INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════

_epd           = None
_refresh_count = 0


def _init_display() -> None:
    global _epd
    if not EPD_AVAILABLE:
        return
    print("Initialising Waveshare EPD...")
    _epd = epd7in5_V2.EPD()
    _epd.init()
    _epd.Clear()
    print("Display ready.")


def _show(img: Image.Image, fast: bool = False) -> None:
    """
    Send img to the display.
    fast=True  -> try init_fast() for quicker menu updates.
    fast=False -> honour full_refresh_every for ghost clearing.
    """
    global _refresh_count

    use_full = (not fast) and (_refresh_count % config.get("full_refresh_every") == 0)

    if not EPD_AVAILABLE:
        img.save("/tmp/literary_clock_preview.png")
        mode = "fast" if fast else ("full" if use_full else "std")
        print(f"  [preview] /tmp/literary_clock_preview.png  [{mode}]")
        _refresh_count += 1
        return

    assert _epd is not None
    if fast:
        try:
            _epd.init_fast()
        except AttributeError:
            _epd.init()
    else:
        _epd.init()

    _epd.display(_epd.getbuffer(img))
    _refresh_count += 1
    mode = "fast" if fast else ("full" if use_full else "std")
    print(f"  display updated #{_refresh_count} [{mode}]")


def _sleep_display() -> None:
    if _epd and EPD_AVAILABLE:
        _epd.sleep()


# ═══════════════════════════════════════════════════════════════════════════════
# DRAWING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _draw_wifi_icon(draw: ImageDraw.ImageDraw,
                    x: int, y: int, connected: bool) -> None:
    """
    Draw a 28x24 WiFi icon at (x, y) top-left.
    Connected:    dot + 3 arcs
    Disconnected: dot + 1 arc + small x overlay
    """
    cx = x + 14
    by = y + 24

    r = 3
    draw.ellipse([cx - r, by - r * 2, cx + r, by], fill=0)

    for radius, show in [(7, True), (13, connected), (19, connected)]:
        if show:
            box = [cx - radius, by - radius, cx + radius, by + radius]
            draw.arc(box, start=225, end=315, fill=0, width=2)

    if not connected:
        ox, oy = x + 20, y + 2
        draw.line([(ox, oy), (ox + 7, oy + 7)], fill=0, width=2)
        draw.line([(ox + 7, oy), (ox, oy + 7)], fill=0, width=2)


def _menu_header(draw: ImageDraw.ImageDraw, title: str) -> None:
    draw.rectangle([0, 0, WIDTH, HEADER_H], fill=0)
    draw.text((MARGIN, HEADER_H // 2), f"  {title}",
              font=fnt_menu_title, fill=255, anchor="lm")


def _menu_footer(draw: ImageDraw.ImageDraw, hints: str) -> None:
    draw.line([(0, HEIGHT - FOOTER_H), (WIDTH, HEIGHT - FOOTER_H)], fill=0, width=1)
    draw.text((MARGIN, HEIGHT - FOOTER_H // 2), hints,
              font=fnt_menu_hint, fill=0, anchor="lm")


# ═══════════════════════════════════════════════════════════════════════════════
# RENDER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def render_clock(h: int, m: int) -> Image.Image:
    """Render the main clock face."""
    quote_text, work, author, _tag = pick_quote(h, m)

    img  = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)

    # WiFi indicator — top-right corner, tucked inside the margin
    if config.get("show_wifi_indicator"):
        _draw_wifi_icon(draw, WIDTH - MARGIN - 28, 8, is_connected())

    # Bottom strip: time + rule + attribution
    time_str = fmt_time(h, m)
    meta_str = (f"{work}  \u2014  {author}" if (work and author)
                else work or author or "")

    meta_bbox = draw.textbbox((0, 0), meta_str or "X", font=fnt_meta)
    time_bbox = draw.textbbox((0, 0), time_str,         font=fnt_time)
    meta_h    = meta_bbox[3] - meta_bbox[1]
    time_h    = time_bbox[3] - time_bbox[1]

    meta_y    = HEIGHT - MARGIN - meta_h
    divider_y = meta_y - 12
    time_y    = divider_y - 10 - time_h

    draw.text((MARGIN, time_y), time_str, font=fnt_time, fill=0)
    if meta_str:
        meta_w = int(draw.textlength(meta_str, font=fnt_meta))
        draw.text((WIDTH - MARGIN - meta_w, meta_y), meta_str,
                  font=fnt_meta, fill=0)
    draw.line([(MARGIN, divider_y), (WIDTH - MARGIN, divider_y)],
              fill=0, width=1)

    # Quote block — shrink font until text fits above the divider
    spans = parse_spans(quote_text)
    fnt_r, fnt_b = _fit_quote(
        draw, spans,
        x=MARGIN, y=MARGIN,
        max_width=WIDTH - 2 * MARGIN,
        max_y=time_y - 20,
    )
    _draw_quote(draw, spans, x=MARGIN, y=MARGIN,
                max_width=WIDTH - 2 * MARGIN, fnt_r=fnt_r, fnt_b=fnt_b)

    return img


def render_menu(menu: Menu) -> Image.Image:
    """Render the menu overlay."""
    img  = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)

    _menu_header(draw, menu.title)
    _menu_footer(draw, "UP   DOWN   OK SELECT   BACK")

    items   = menu.items
    y_start = HEADER_H + 14
    y_max   = HEIGHT - FOOTER_H - 8

    for i, item in enumerate(items):
        iy = y_start + i * MENU_ITEM_H
        if iy + MENU_ITEM_H > y_max:
            break

        selected = (i == menu.cursor)
        if selected:
            draw.rectangle(
                [MARGIN // 2, iy + 2, WIDTH - MARGIN // 2, iy + MENU_ITEM_H - 2],
                fill=0,
            )
            draw.text((MARGIN * 2, iy + MENU_ITEM_H // 2), item.label,
                      font=fnt_menu_item, fill=255, anchor="lm")
        else:
            draw.text((MARGIN * 2, iy + MENU_ITEM_H // 2), item.label,
                      font=fnt_menu_item, fill=0, anchor="lm")

    return img


def render_time_set(h: int, m: int, field: str) -> Image.Image:
    """
    Render the manual time-set screen.
    field: 'h' highlights hours, 'm' highlights minutes.
    """
    img  = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)

    _menu_header(draw, "SET TIME")
    hint = ("UP/DOWN Adjust hour    OK Next    BACK Cancel" if field == "h"
            else "UP/DOWN Adjust minute    OK Save    BACK Cancel")
    _menu_footer(draw, hint)

    h_str, m_str, colon = f"{h:02d}", f"{m:02d}", ":"

    h_w = int(draw.textlength(h_str,  font=fnt_settime))
    m_w = int(draw.textlength(m_str,  font=fnt_settime))
    c_w = int(draw.textlength(colon,  font=fnt_settime))
    PAD = 18
    tot = h_w + c_w + m_w + PAD * 2

    bbox    = draw.textbbox((0, 0), h_str, font=fnt_settime)
    digit_h = bbox[3] - bbox[1]
    cy      = (HEIGHT + HEADER_H - FOOTER_H) // 2
    y       = cy - digit_h // 2

    h_x = WIDTH // 2 - tot // 2
    c_x = h_x + h_w + PAD
    m_x = c_x + c_w + PAD
    BP  = 10

    if field == "h":
        draw.rectangle([h_x-BP, y-BP, h_x+h_w+BP, y+digit_h+BP], fill=0)
        draw.text((h_x, y), h_str, font=fnt_settime, fill=255)
    else:
        draw.text((h_x, y), h_str, font=fnt_settime, fill=0)

    draw.text((c_x, y), colon, font=fnt_settime, fill=0)

    if field == "m":
        draw.rectangle([m_x-BP, y-BP, m_x+m_w+BP, y+digit_h+BP], fill=0)
        draw.text((m_x, y), m_str, font=fnt_settime, fill=255)
    else:
        draw.text((m_x, y), m_str, font=fnt_settime, fill=0)

    return img


def render_message(text: str, subtext: str = "") -> Image.Image:
    """Render a brief centred status message."""
    img  = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)
    cy   = HEIGHT // 2
    draw.text((WIDTH // 2, cy - 22), text,
              font=fnt_menu_item, fill=0, anchor="mm")
    if subtext:
        draw.text((WIDTH // 2, cy + 22), subtext,
                  font=fnt_menu_hint, fill=0, anchor="mm")
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# MENU BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_menu() -> Menu:
    """
    ──────────────────────────────────────────────────────────────────────────
    ADD NEW MENU ITEMS HERE.
    See menu.py for full documentation on MenuItem parameters and return values.
    ──────────────────────────────────────────────────────────────────────────
    """
    return Menu("MENU", [

        MenuItem(
            label  = "Set Time Manually",
            action = lambda: STATE_SET_H,
        ),

        MenuItem(
            label  = "Sync Time via NTP",
            action = _action_ntp_sync,
        ),

        MenuItem(
            label  = lambda: f"WiFi Indicator: {'ON' if config.get('show_wifi_indicator') else 'OFF'}",
            action = lambda: (config.toggle("show_wifi_indicator"), None)[1],
        ),

        MenuItem(
            label  = lambda: f"Quote Interval: {config.get('quote_interval')} min",
            action = _action_cycle_interval,
        ),

        MenuItem(
            label  = "Return to Clock",
            action = lambda: STATE_CLOCK,
        ),

        # ── Add new items above this line ─────────────────────────────────────
    ])


def _action_ntp_sync():
    invalidate_wifi_cache()
    if not is_connected():
        return MsgResult("No internet connection", "Check WiFi and try again.")
    sync_ntp()
    return MsgResult("Time synced", "Manual offset cleared.")


def _action_cycle_interval():
    """Cycle quote_interval through 1 -> 5 -> 10 -> 1."""
    options = [1, 5, 10]
    current = config.get("quote_interval") or 1
    nxt = options[(options.index(current) + 1) % len(options)] if current in options else 1
    config.set_val("quote_interval", nxt)
    return None   # stay in menu; label updates automatically


# ═══════════════════════════════════════════════════════════════════════════════
# GRACEFUL SHUTDOWN
# ═══════════════════════════════════════════════════════════════════════════════

def _shutdown(sig, frame):
    print("\nShutting down...")
    _sleep_display()
    if _gpio_ok:
        try:
            import RPi.GPIO as GPIO
            GPIO.cleanup()
        except Exception:
            pass
    sys.exit(0)


signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 52)
    print("  Literary Clock -- Waveshare 7.5\" / Raspberry Pi")
    print("=" * 52)

    config.load()
    _reload_fonts()
    _init_quotes()
    _setup_buttons()
    _setup_keyboard()
    _init_display()

    menu     = build_menu()
    state    = STATE_CLOCK
    last_key: str | None = None

    edit_h = edit_m = 0

    msg_text    = ""
    msg_subtext = ""
    msg_until   = 0.0

    def enter(new_state: str, **kw) -> None:
        nonlocal state, edit_h, edit_m, msg_text, msg_subtext, msg_until, last_key
        state = new_state
        if new_state == STATE_CLOCK:
            last_key = None
        elif new_state == STATE_MENU:
            menu.reset()
        elif new_state == STATE_SET_H:
            edit_h, edit_m = get_display_time()
        elif new_state == STATE_MESSAGE:
            msg_text    = kw.get("text", "")
            msg_subtext = kw.get("subtext", "")
            msg_until   = time.monotonic() + MESSAGE_TTL

    # First paint
    h, m = get_display_time()
    _show(render_clock(h, m))
    _sleep_display()
    last_key = f"{h:02d}:{m:02d}"
    print(f"Clock running. btn_menu = GPIO {config.get('btn_menu')} to open menu.")

    while True:
        btn = _next_button()

        # Auto-expire message
        if state == STATE_MESSAGE and time.monotonic() >= msg_until:
            enter(STATE_CLOCK)

        # ── CLOCK ─────────────────────────────────────────────────────────────
        if state == STATE_CLOCK:
            if btn == "MENU":
                enter(STATE_MENU)
                _show(render_menu(menu), fast=True)
                _sleep_display()
            else:
                h, m = get_display_time()
                key  = f"{h:02d}:{m:02d}"
                if key != last_key:
                    print(f"\n[{key}] Rendering...")
                    _show(render_clock(h, m))
                    _sleep_display()
                    last_key = key

        # ── MENU ──────────────────────────────────────────────────────────────
        elif state == STATE_MENU:
            if btn == "UP":
                menu.move(-1)
                _show(render_menu(menu), fast=True)
                _sleep_display()
            elif btn == "DOWN":
                menu.move(1)
                _show(render_menu(menu), fast=True)
                _sleep_display()
            elif btn == "SELECT":
                result = menu.select()
                if isinstance(result, MsgResult):
                    enter(STATE_MESSAGE, text=result.text, subtext=result.subtext)
                    _show(render_message(msg_text, msg_subtext), fast=True)
                    _sleep_display()
                elif result == STATE_CLOCK:
                    enter(STATE_CLOCK)
                    h, m = get_display_time()
                    _show(render_clock(h, m))
                    _sleep_display()
                elif result == STATE_SET_H:
                    enter(STATE_SET_H)
                    _show(render_time_set(edit_h, edit_m, "h"), fast=True)
                    _sleep_display()
                else:
                    # None (toggle) — stay in menu, re-render for updated labels
                    _show(render_menu(menu), fast=True)
                    _sleep_display()
            elif btn == "MENU":
                enter(STATE_CLOCK)
                h, m = get_display_time()
                _show(render_clock(h, m))
                _sleep_display()

        # ── SET TIME: HOURS ───────────────────────────────────────────────────
        elif state == STATE_SET_H:
            if btn == "UP":
                edit_h = (edit_h + 1) % 24
                _show(render_time_set(edit_h, edit_m, "h"), fast=True)
                _sleep_display()
            elif btn == "DOWN":
                edit_h = (edit_h - 1) % 24
                _show(render_time_set(edit_h, edit_m, "h"), fast=True)
                _sleep_display()
            elif btn == "SELECT":
                enter(STATE_SET_M)
                _show(render_time_set(edit_h, edit_m, "m"), fast=True)
                _sleep_display()
            elif btn == "MENU":
                enter(STATE_CLOCK)
                h, m = get_display_time()
                _show(render_clock(h, m))
                _sleep_display()

        # ── SET TIME: MINUTES ─────────────────────────────────────────────────
        elif state == STATE_SET_M:
            if btn == "UP":
                edit_m = (edit_m + 1) % 60
                _show(render_time_set(edit_h, edit_m, "m"), fast=True)
                _sleep_display()
            elif btn == "DOWN":
                edit_m = (edit_m - 1) % 60
                _show(render_time_set(edit_h, edit_m, "m"), fast=True)
                _sleep_display()
            elif btn == "SELECT":
                apply_manual_time(edit_h, edit_m)
                enter(STATE_MESSAGE,
                      text=f"Time set to {edit_h:02d}:{edit_m:02d}",
                      subtext="Saved to config.json")
                _show(render_message(msg_text, msg_subtext), fast=True)
                _sleep_display()
            elif btn == "MENU":
                enter(STATE_CLOCK)
                h, m = get_display_time()
                _show(render_clock(h, m))
                _sleep_display()

        # ── MESSAGE (any button skips the wait) ───────────────────────────────
        elif state == STATE_MESSAGE:
            if btn:
                enter(STATE_CLOCK)
                h, m = get_display_time()
                _show(render_clock(h, m))
                _sleep_display()

        time.sleep(0.05)


if __name__ == "__main__":
    main()