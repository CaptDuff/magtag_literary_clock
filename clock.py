#!/usr/bin/env python3
"""
Raspberry Pi + Waveshare 7.5" e-ink (800×480) Literary Quote Clock
Port of Adafruit MagTag CircuitPython version → Python 3 / RPi / Pillow

Hardware:
  - Raspberry Pi (any model with SPI)
  - Waveshare 7.5" e-ink HAT V2 (800×480, black/white)
    Connects directly to the 40-pin GPIO header.

Software dependencies:
  pip install Pillow RPi.GPIO
  Waveshare EPD library — clone into ./lib/:
    git clone https://github.com/waveshare/e-Paper
    cp -r e-Paper/RaspberryPi_JetsonNano/python/lib ./lib

CSV format (pipe-delimited, same file as MagTag version):
  hhmm|quote with ^bold span^|Work Title|Author|tag

Bold spans are rendered in a heavier font weight — same visual idea as
the MagTag faux-bold (double-draw) trick, just done properly with PIL.
"""

import time
import datetime
import os
import sys
import signal

from PIL import Image, ImageDraw, ImageFont

# ─── Waveshare library path ───────────────────────────────────────────────────
# Expects the waveshare_epd package at ./lib/waveshare_epd/
LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

try:
    from waveshare_epd import epd7in5_V2
    EPD_AVAILABLE = True
except ImportError:
    EPD_AVAILABLE = False
    print("⚠  waveshare_epd not found — running in PREVIEW MODE (saves PNG to /tmp/)")

# ─── Configuration ────────────────────────────────────────────────────────────

CSV_PATH    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quotes.csv")

# Fonts — DejaVu ships with Raspberry Pi OS; adjust paths if needed.
# Download a nicer serif (e.g. Libre Baskerville) for a more book-like look.
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
FONT_BOLD    = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_ITALIC  = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"

FONT_SIZE_QUOTE = 38   # main quote text
FONT_SIZE_TIME  = 30   # HH:MM in bottom corner
FONT_SIZE_META  = 22   # "Work — Author" attribution

WIDTH   = 800
HEIGHT  = 480
MARGIN  = 36           # px padding on all sides
LINE_SPACING = 10      # extra px between wrapped lines

# E-ink ghosting management:
# Run a full (slow, ghost-clearing) refresh every N updates,
# fast refresh the rest of the time.
FULL_REFRESH_EVERY = 8

# How often to check if the minute has changed (seconds).
# Lower = more responsive to button presses; higher = less CPU churn.
POLL_INTERVAL = 10

# ─── Optional GPIO time-adjustment buttons ────────────────────────────────────
# Set BCM pin numbers for physical buttons wired to your Pi, or leave as None.
# Buttons should connect the pin to GND when pressed (internal pull-up enabled).
# On the Pi these are purely optional — the system clock is set by NTP.
# Use them if you want a manual offset (e.g. displaying a different timezone).
BTN_HOUR_UP   = None   # e.g. 17  →  +1 hour
BTN_HOUR_DOWN = None   # e.g. 27  →  -1 hour
BTN_MIN_UP    = None   # e.g. 22  →  +1 minute
BTN_MIN_DOWN  = None   # e.g. 23  →  -1 minute

# ─── Font loading ─────────────────────────────────────────────────────────────

def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except (IOError, OSError):
        print(f"  Font not found: {path} — falling back to PIL default")
        return ImageFont.load_default()

fnt_quote  = load_font(FONT_REGULAR, FONT_SIZE_QUOTE)
fnt_bold   = load_font(FONT_BOLD,    FONT_SIZE_QUOTE)
fnt_time   = load_font(FONT_BOLD,    FONT_SIZE_TIME)
fnt_meta   = load_font(FONT_ITALIC,  FONT_SIZE_META)

# ─── CSV loader ───────────────────────────────────────────────────────────────

def load_quotes(path: str) -> dict:
    """
    Returns dict: {'HH:MM': [(quote_with_carets, work, author, tag), …]}
    Handles the slightly malformed rows in the original CSV (outer quotes,
    trailing commas from spreadsheet export, etc.).
    """
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

                # Normalise hhmm → 'HH:MM'
                digits = hhmm.replace(":", "")
                if len(digits) == 3:
                    digits = "0" + digits
                if len(digits) == 4:
                    hhmm = f"{digits[:2]}:{digits[2:]}"

                if len(hhmm) == 5 and hhmm[2] == ":":
                    mapping.setdefault(hhmm, []).append((quote, work, author, tag))
    except (OSError, FileNotFoundError) as exc:
        print(f"Could not load quotes from {path}: {exc}")
    return mapping


QUOTES = load_quotes(CSV_PATH)
print(f"Loaded {sum(len(v) for v in QUOTES.values())} quotes across {len(QUOTES)} minutes.")

if not QUOTES:
    QUOTES = {"12:00": [("It is ^now^. The clock has no other wisdom to offer.", "Literary Clock", "System", "")]}

# ─── Bold-span parsing ────────────────────────────────────────────────────────

def parse_spans(text: str) -> list[tuple[str, bool]]:
    """
    Split 'some ^highlighted^ text' into [(str, is_bold), …].
    Strips any unmatched carets.
    """
    parts = text.split("^")
    return [(part, i % 2 == 1) for i, part in enumerate(parts) if part]

# ─── Word-wrap with mixed bold/normal spans ───────────────────────────────────

def wrap_spans(
    spans: list[tuple[str, bool]],
    draw: ImageDraw.ImageDraw,
    max_width: int,
) -> list[list[tuple[str, bool]]]:
    """
    Word-wrap a list of (text, bold) spans into lines that fit max_width.
    Returns a list of lines; each line is a list of (word, bold) pairs.
    """
    words: list[tuple[str, bool]] = []
    for text, bold in spans:
        # Split on whitespace but keep the bold flag per word
        for w in text.split():
            if w:
                words.append((w, bold))

    lines: list[list[tuple[str, bool]]] = []
    current: list[tuple[str, bool]] = []
    current_w: float = 0.0

    space_w = draw.textlength(" ", font=fnt_quote)

    for word, bold in words:
        font = fnt_bold if bold else fnt_quote
        word_w = draw.textlength(word, font=font)
        gap    = space_w if current else 0.0
        if current and (current_w + gap + word_w) > max_width:
            lines.append(current)
            current   = [(word, bold)]
            current_w = word_w
        else:
            current.append((word, bold))
            current_w += gap + word_w

    if current:
        lines.append(current)

    return lines


def draw_quote_text(
    draw: ImageDraw.ImageDraw,
    spans: list[tuple[str, bool]],
    x: int,
    y: int,
    max_width: int,
) -> int:
    """Draw word-wrapped quote. Returns the y coordinate after the last line."""
    lines = wrap_spans(spans, draw, max_width)

    # Use a consistent line height regardless of bold/normal mix
    bbox = draw.textbbox((0, 0), "Ágjy", font=fnt_quote)
    line_h = bbox[3] - bbox[1] + LINE_SPACING

    space_w = int(draw.textlength(" ", font=fnt_quote))

    for line in lines:
        cx = x
        for i, (word, bold) in enumerate(line):
            font = fnt_bold if bold else fnt_quote
            draw.text((cx, y), word, font=font, fill=0)
            cx += int(draw.textlength(word, font=font))
            if i < len(line) - 1:
                cx += space_w
        y += line_h

    return y

# ─── Quote selection ──────────────────────────────────────────────────────────

def pick_quote(h: int, m: int) -> tuple:
    """Pick a quote for (h, m). Falls back to nearest earlier minute if missing."""
    key = f"{h:02d}:{m:02d}"
    lst = QUOTES.get(key)

    if not lst:
        # Walk back up to 30 minutes to find the nearest entry
        base = datetime.datetime(2000, 1, 1, h, m)
        for delta in range(1, 31):
            prev = base - datetime.timedelta(minutes=delta)
            alt  = f"{prev.hour:02d}:{prev.minute:02d}"
            lst  = QUOTES.get(alt)
            if lst:
                break

    if not lst:
        # Last resort — any random quote from the collection
        lst = next(iter(QUOTES.values()))

    # Rotate by hour so multiple quotes per minute stay varied
    return lst[h % len(lst)]

# ─── Frame renderer ───────────────────────────────────────────────────────────

def render_frame(h: int, m: int) -> Image.Image:
    """Render a complete 800×480 1-bit image for the given time."""
    quote_text, work, author, _tag = pick_quote(h, m)

    img  = Image.new("1", (WIDTH, HEIGHT), 255)  # white background
    draw = ImageDraw.Draw(img)

    # ── Bottom strip: time + attribution ──────────────────────────────────────
    time_str = f"{h:02d}:{m:02d}"
    meta_str = f"{work}  —  {author}" if (work and author) else work or author

    meta_bbox = draw.textbbox((0, 0), meta_str or "X", font=fnt_meta)
    meta_h    = meta_bbox[3] - meta_bbox[1]
    time_bbox = draw.textbbox((0, 0), time_str, font=fnt_time)
    time_h    = time_bbox[3] - time_bbox[1]

    meta_y    = HEIGHT - MARGIN - meta_h
    divider_y = meta_y - 12
    time_y    = divider_y - 10 - time_h
    quote_max_y = time_y - 20  # quote block must not exceed this

    # Draw time (left-aligned)
    draw.text((MARGIN, time_y), time_str, font=fnt_time, fill=0)

    # Draw attribution (right-aligned for a typographic touch)
    if meta_str:
        meta_w = draw.textlength(meta_str, font=fnt_meta)
        draw.text((WIDTH - MARGIN - meta_w, meta_y), meta_str, font=fnt_meta, fill=0)

    # Thin rule above time/meta area
    draw.line([(MARGIN, divider_y), (WIDTH - MARGIN, divider_y)], fill=0, width=1)

    # ── Quote block ───────────────────────────────────────────────────────────
    spans = parse_spans(quote_text)
    draw_quote_text(
        draw, spans,
        x=MARGIN, y=MARGIN,
        max_width=WIDTH - 2 * MARGIN,
    )

    return img

# ─── E-ink display interface ─────────────────────────────────────────────────

epd          = None
_refresh_count = 0

def init_display():
    global epd
    if not EPD_AVAILABLE:
        return
    print("Initialising Waveshare EPD…")
    epd = epd7in5_V2.EPD()
    epd.init()
    epd.Clear()
    print("Display ready.")


def show_image(img: Image.Image, force_full: bool = False):
    """Send an image to the display (full or fast refresh)."""
    global _refresh_count
    use_full = force_full or (_refresh_count % FULL_REFRESH_EVERY == 0)

    if not EPD_AVAILABLE:
        preview_path = "/tmp/literary_clock_preview.png"
        img.save(preview_path)
        print(f"  [preview] saved → {preview_path}  ({'full' if use_full else 'fast'})")
        _refresh_count += 1
        return

    assert epd is not None
    if use_full:
        epd.init()
    else:
        epd.init_fast()

    epd.display(epd.getbuffer(img))
    _refresh_count += 1
    print(f"  display updated (refresh #{_refresh_count}, {'full' if use_full else 'fast'})")


def sleep_display():
    if epd and EPD_AVAILABLE:
        epd.sleep()

# ─── Optional GPIO buttons ────────────────────────────────────────────────────

_gpio_ok      = False
_time_offset  = datetime.timedelta()

_btn_pins = [p for p in [BTN_HOUR_UP, BTN_HOUR_DOWN, BTN_MIN_UP, BTN_MIN_DOWN] if p is not None]

if _btn_pins:
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        for pin in _btn_pins:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        _gpio_ok = True
        print(f"GPIO buttons enabled on pins: {_btn_pins}")
    except ImportError:
        print("RPi.GPIO not available — buttons disabled.")


def read_buttons():
    """Check buttons, adjust _time_offset, debounce with short sleep."""
    global _time_offset
    if not _gpio_ok:
        return
    import RPi.GPIO as GPIO
    if BTN_HOUR_UP   and not GPIO.input(BTN_HOUR_UP):
        _time_offset += datetime.timedelta(hours=1);   time.sleep(0.35)
    if BTN_HOUR_DOWN and not GPIO.input(BTN_HOUR_DOWN):
        _time_offset -= datetime.timedelta(hours=1);   time.sleep(0.35)
    if BTN_MIN_UP    and not GPIO.input(BTN_MIN_UP):
        _time_offset += datetime.timedelta(minutes=1); time.sleep(0.35)
    if BTN_MIN_DOWN  and not GPIO.input(BTN_MIN_DOWN):
        _time_offset -= datetime.timedelta(minutes=1); time.sleep(0.35)


def get_display_time() -> tuple[int, int]:
    now = datetime.datetime.now() + _time_offset
    return now.hour, now.minute

# ─── Graceful shutdown ────────────────────────────────────────────────────────

def _shutdown(sig, frame):
    print("\nShutting down…")
    sleep_display()
    if _gpio_ok:
        import RPi.GPIO as GPIO
        GPIO.cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print(" Literary Clock — Waveshare 7.5\" / Raspberry Pi")
    print("=" * 50)
    init_display()

    last_key: str | None = None

    while True:
        read_buttons()
        h, m    = get_display_time()
        key     = f"{h:02d}:{m:02d}"

        if key != last_key:
            print(f"\n[{key}] Rendering…")
            img = render_frame(h, m)
            show_image(img)
            sleep_display()   # low-power standby between refreshes
            last_key = key

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()