# MagTag Offline Literary Quote Clock (CSV + ^bold^ support)
# - No networking
# - Loads quotes from /quotes.csv (pipe-delimited: hhmm|quote|work|author|tag)
# - Set time with buttons A/B/C/D
# - Highlights the ^time phrase^ by drawing it twice (faux bold)

import time
import board
import displayio
import terminalio
import rtc
import keypad
from adafruit_display_text import label
from adafruit_display_shapes.rect import Rect

# ------------- Config -------------
UPDATE_MINUTES = 5       # e-ink refresh bucket
TIME_24H = True          # False for 12h with AM/PM
CSV_PATH = "/quotes.csv"
MARGIN = 10
MAX_WIDTH = None         # will be set after display init

# ------------- Display setup -------------
display = board.DISPLAY
main = displayio.Group()
display.root_group = main   # modern API (replaces display.show)

bg = Rect(0, 0, display.width, display.height, fill=0xFFFFFF)
main.append(bg)

# container just for the quote so we can rebuild it each render
quote_group = displayio.Group(x=0, y=0)
main.append(quote_group)

# Bottom-left time (will sit just above meta)
time_label = label.Label(
    terminalio.FONT, text="", color=0x000000,
    anchor_point=(0, 1), anchored_position=(0, 0)  # we'll position in render()
)

# Bottom-left meta (author - book) at very bottom
meta_label = label.Label(
    terminalio.FONT, text="", color=0x000000,
    anchor_point=(0, 1), anchored_position=(0, 0)  # we'll position in render()
)

main.append(time_label)
main.append(meta_label)

MAX_WIDTH = display.width - 2*MARGIN

# ------------- Buttons -------------
keys = keypad.Keys(
    (board.BUTTON_A, board.BUTTON_B, board.BUTTON_C, board.BUTTON_D),
    value_when_pressed=False, pull=True
)

# ------------- RTC helpers -------------
rtc_dev = rtc.RTC()

def get_now():
    return rtc_dev.datetime

def set_now(year, month, mday, hour, minute, second=0, wday=-1, yday=-1, isdst=-1):
    rtc_dev.datetime = time.struct_time((year, month, mday, hour, minute, second, wday, yday, isdst))

def ensure_default_time_if_unset():
    now = get_now()
    if now.tm_year < 2023:
        set_now(2025, 1, 1, 12, 0, 0)

ensure_default_time_if_unset()

# ------------- Safe refresh (avoid "refresh too soon") -------------
_last_refresh = 0
MIN_REFRESH_GAP = 0.5  # seconds

def safe_refresh():
    global _last_refresh
    nowm = time.monotonic()
    if nowm - _last_refresh < MIN_REFRESH_GAP:
        return
    try:
        display.refresh()
        _last_refresh = nowm
    except RuntimeError:
        # Still busy; wait a tad and try once more
        time.sleep(0.5)
        try:
            display.refresh()
            _last_refresh = time.monotonic()
        except RuntimeError:
            # Skip; next loop/render will catch up
            pass

# ------------- CSV loader -------------
def load_quotes_csv(path):
    """
    Returns dict: {'HH:MM': [(raw_quote_with_carets, work, author, tag), ...], ...}
    We keep the ^markers^ so we can bold that span later.
    """
    mapping = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.rstrip("\r\n")
                if not raw or raw.startswith("#"):
                    continue
                parts = raw.split("|", 4)
                if len(parts) < 5:
                    continue
                hhmm, quote, work, author, tag = parts[0], parts[1], parts[2], parts[3], parts[4]
                hhmm = hhmm.strip()
                if len(hhmm) == 4 and hhmm[1] == ":":
                    h,m = hhmm.split(":")
                    hhmm = f"{int(h):02d}:{int(m):02d}"
                if len(hhmm) == 5 and hhmm[2] == ":":
                    mapping.setdefault(hhmm, []).append((quote, work, author, tag))
    except OSError:
        pass
    return mapping

QUOTES_BY_HHMM = load_quotes_csv(CSV_PATH)
if not QUOTES_BY_HHMM:
    QUOTES_BY_HHMM = {
        "00:00": [("^Midnight^.", "Fallback", "System", "unknown")],
        "12:00": [("High ^noon^.", "Fallback", "System", "unknown")]
    }

# ------------- Text layout helpers -------------
def split_caret_span(text):
    """Return (pre, mid, post) where mid is the ^highlighted^ span, or (text, None, "") if none."""
    a = text.find("^")
    if a == -1:
        return text, None, ""
    b = text.find("^", a+1)
    if b == -1:
        # only one caret found; treat as no highlight, strip it
        t = text.replace("^","")
        return t, None, ""
    pre = text[:a].replace("^","")
    mid = text[a+1:b].replace("^","")
    post = text[b+1:].replace("^","")
    return pre, mid, post

def measure_token(wtxt):
    """Create a tiny label to measure the pixel width of a token (no draw cost; reused)."""
    temp = label.Label(terminalio.FONT, text=wtxt)
    bb = temp.bounding_box  # (x, y, w, h)
    return bb[2], bb[3]

def layout_quote_into_group(qgroup, quote_text, y_start, y_max=None):
    # ... (your caret-split + faux-bold logic stays the same) ...
    while len(qgroup):
        qgroup.pop()

    pre, mid, post = split_caret_span(quote_text)
    tokens = []
    if pre:  tokens += [(w, False) for w in pre.split()]
    if mid is not None: tokens += [(mid, True)]
    if post: tokens += [(w, False) for w in post.split()]

    def measure(wtxt):
        lab = label.Label(quote_font if 'quote_font' in globals() else terminalio.FONT, text=wtxt)
        bb = lab.bounding_box
        return bb[2], bb[3]

    space_w, line_h = measure(" ")
    x = MARGIN
    y = y_start
    line = []
    line_w = 0

    def flush_line():
        nonlocal x, y, line
        x = MARGIN
        # before placing, check height vs y_max
        if y_max is not None and (y + line_h) > y_max:
            line.clear()
            return False
        for txt, bold in line:
            font = quote_font if 'quote_font' in globals() else terminalio.FONT
            lab = label.Label(font, text=txt, color=0x000000, anchor_point=(0,0), anchored_position=(x,y))
            qgroup.append(lab)
            if bold:
                lab2 = label.Label(font, text=txt, color=0x000000, anchor_point=(0,0), anchored_position=(x+1,y))
                qgroup.append(lab2)
            w, _ = lab.bounding_box[2], lab.bounding_box[3]
            x += w + space_w
        line.clear()
        return True

    for txt, bold in tokens:
        w, _ = measure(txt)
        next_w = (w if not line else (line_w + space_w + w))
        if line and (MARGIN + next_w + MARGIN) > display.width:
            if not flush_line():
                return
            y += line_h + 2
            line = [(txt, bold)]
            line_w = w
        else:
            line.append((txt, bold))
            line_w = next_w if line else w

    if line:
        flush_line()


def fmt_time(h, m):
    if TIME_24H:
        return f"{h:02d}:{m:02d}"
    suf = "AM" if h < 12 else "PM"
    hh = h % 12
    if hh == 0:
        hh = 12
    return f"{hh}:{m:02d} {suf}"
    
# Replace unsupported Unicode so tiny fonts don't crash
def sanitize_ascii(s: str) -> str:
    if not s:
        return s
    # Common replacements for bitmap/terminal fonts
    repl = {
        "—": "-", "–": "-", "…": "...",
        "“": '"', "”": '"', "‘": "'", "’": "'",
        "•": "*", "·": "*",
        "\u00a0": " ",  # non-breaking space
    }
    out = []
    for ch in s:
        out.append(repl.get(ch, ch if ord(ch) < 128 else "?"))
    return "".join(out)


# ------------- Quote selection -------------
_last_bucket = None

def pick_for_time(h, m):
    key = f"{h:02d}:{m:02d}"
    lst = QUOTES_BY_HHMM.get(key)
    if not lst:
        bucket_m = (m // UPDATE_MINUTES) * UPDATE_MINUTES
        lst = QUOTES_BY_HHMM.get(f"{h:02d}:{bucket_m:02d}")
    if not lst:
        any_key = next(iter(QUOTES_BY_HHMM))
        lst = QUOTES_BY_HHMM[any_key]
    idx = h % len(lst)  # rotate by hour if several options
    return lst[idx]

def render(force=False, live_preview_time=None):
    global _last_bucket
    now = get_now()
    h, m = (live_preview_time if live_preview_time else (now.tm_hour, now.tm_min))

    bucket = (m // UPDATE_MINUTES) * UPDATE_MINUTES
    if not force and live_preview_time is None and bucket == _last_bucket:
        return

    quote_text, work, author, tag = pick_for_time(h, m)

    # 1) Compose & place bottom-left meta
    meta_text = sanitize_ascii(f"{work} - {author}")
    meta_label.text = meta_text
    meta_y = display.height - MARGIN
    meta_label.anchored_position = (MARGIN, meta_y)
    meta_h = meta_label.bounding_box[3]

    # 2) Compose & place time directly above meta
    time_text = sanitize_ascii("Time: " + fmt_time(h, m))
    time_label.text = time_text
    time_h = time_label.bounding_box[3]
    time_y = meta_y - meta_h - 4  # 4px padding
    time_label.anchored_position = (MARGIN, time_y)

    # 3) Quote fills from top down, leaving space above time
    quote_top = MARGIN
    quote_bottom_limit = time_y - 8  # extra padding above time
    layout_quote_into_group(quote_group, quote_text, y_start=quote_top, y_max=quote_bottom_limit)

    safe_refresh()
    if live_preview_time is None:
        _last_bucket = bucket



# First paint
render(force=True)

# ------------- Set-time UI -------------
SET_NONE, SET_H, SET_M, SET_SAVE = range(4)
set_mode = SET_NONE
edit_h = 0
edit_m = 0

def enter_set():
    global set_mode, edit_h, edit_m
    now = get_now()
    edit_h, edit_m = now.tm_hour, now.tm_min
    set_mode = SET_H
    meta_label.text = "Set H  (A next  B+  C-  D save)"

def cycle_field():
    global set_mode
    if set_mode == SET_H:
        set_mode = SET_M
        meta_label.text = "Set M  (A next  B+  C-  D save)"
    elif set_mode == SET_M:
        set_mode = SET_SAVE
        meta_label.text = "Press D to Save (A cycles)"
    elif set_mode == SET_SAVE:
        set_mode = SET_H
        meta_label.text = "Set H  (A next  B+  C-  D save)"

def commit_time():
    global set_mode
    now = get_now()
    set_now(now.tm_year, now.tm_mon, now.tm_mday, edit_h, edit_m, 0)
    set_mode = SET_NONE
    meta_label.text = ""
    render(force=True)

# ------------- Main loop -------------
last_minute_seen = -1

while True:
    evt = keys.events.get()
    if evt and evt.pressed:
        if set_mode == SET_NONE:
            if evt.key_number == 0:  # A: enter set mode
                enter_set()
        else:
            if evt.key_number == 0:      # A: next field
                cycle_field()
            elif evt.key_number == 1:    # B: +
                if set_mode == SET_H:
                    edit_h = (edit_h + 1) % 24
                elif set_mode == SET_M:
                    edit_m = (edit_m + 1) % 60
            elif evt.key_number == 2:    # C: -
                if set_mode == SET_H:
                    edit_h = (edit_h - 1) % 24
                elif set_mode == SET_M:
                    edit_m = (edit_m - 1) % 60
            elif evt.key_number == 3:    # D: save
                commit_time()

            # live preview while editing (no crash on fast taps)
            if set_mode in (SET_H, SET_M):
                render(live_preview_time=(edit_h, edit_m))

    now = get_now()
    if set_mode == SET_NONE and now.tm_min != last_minute_seen:
        render()
        last_minute_seen = now.tm_min

    time.sleep(0.1)
