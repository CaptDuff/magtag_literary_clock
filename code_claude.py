# MagTag Offline Literary Quote Clock — Battery Optimized
# - Deep sleeps between updates (~250uA while asleep)
# - Wakes on TimeAlarm (next minute) OR PinAlarm (any button press)
# - Button A: toggle hour/minute field
# - Button B: +1  /  Button C: -1  /  Button D: save & sleep
#
# HOW DEEP SLEEP WORKS HERE:
#   code.py runs top-to-bottom every wake cycle.
#   alarm.wake_alarm tells us what woke us (time vs button).
#   RTC persists through deep sleep so the clock keeps time.

import time
import board
import displayio
import terminalio
import rtc
import alarm
import digitalio
import supervisor
from adafruit_display_text import label
from adafruit_display_shapes.rect import Rect

# ------------------------------------------------------------------ #
#  Config
# ------------------------------------------------------------------ #
UPDATE_MINUTES = 5        # quote bucket size (quotes keyed to every N min)
TIME_24H       = True
CSV_PATH       = "/quotes.csv"
MARGIN         = 10
SLEEP_SECONDS  = 60       # max sleep; aligned to minute boundary below

# ------------------------------------------------------------------ #
#  Disable WiFi radio — saves power even when WiFi is never used
# ------------------------------------------------------------------ #
try:
    import wifi
    wifi.radio.enabled = False
except Exception:
    pass

# ------------------------------------------------------------------ #
#  Display — disable auto_refresh so we control every e-ink write
# ------------------------------------------------------------------ #
display = board.DISPLAY
display.auto_refresh = False

main        = displayio.Group()
display.root_group = main

bg          = Rect(0, 0, display.width, display.height, fill=0xFFFFFF)
main.append(bg)

quote_group = displayio.Group()
main.append(quote_group)

time_label  = label.Label(terminalio.FONT, text="", color=0x000000,
                          anchor_point=(0, 1), anchored_position=(0, 0))
meta_label  = label.Label(terminalio.FONT, text="", color=0x000000,
                          anchor_point=(0, 1), anchored_position=(0, 0))
main.append(time_label)
main.append(meta_label)

# ------------------------------------------------------------------ #
#  RTC helpers
# ------------------------------------------------------------------ #
rtc_dev = rtc.RTC()

def get_now():
    return rtc_dev.datetime

def set_now(h, m, s=0):
    n = get_now()
    rtc_dev.datetime = time.struct_time(
        (n.tm_year, n.tm_mon, n.tm_mday, h, m, s, -1, -1, -1))

def ensure_sane_time():
    if get_now().tm_year < 2023:
        set_now(12, 0, 0)

ensure_sane_time()

# ------------------------------------------------------------------ #
#  Safe display refresh (with busy-guard)
# ------------------------------------------------------------------ #
def safe_refresh():
    try:
        display.refresh()
    except RuntimeError:
        time.sleep(0.5)
        try:
            display.refresh()
        except RuntimeError:
            pass

# ------------------------------------------------------------------ #
#  CSV loader
# ------------------------------------------------------------------ #
def load_quotes_csv(path):
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
                hhmm, quote, work, author, tag = parts
                hhmm = hhmm.strip()
                if len(hhmm) == 4 and hhmm[1] == ":":
                    h, m = hhmm.split(":")
                    hhmm = f"{int(h):02d}:{int(m):02d}"
                if len(hhmm) == 5 and hhmm[2] == ":":
                    mapping.setdefault(hhmm, []).append(
                        (quote, work, author, tag))
    except OSError:
        pass
    return mapping

QUOTES = load_quotes_csv(CSV_PATH) or {
    "00:00": [("^Midnight^.", "Fallback", "System", "")],
    "12:00": [("High ^noon^.", "Fallback", "System", "")],
}

# ------------------------------------------------------------------ #
#  Text helpers
# ------------------------------------------------------------------ #
def sanitize_ascii(s):
    if not s:
        return s
    repl = {
        "\u2014": "-", "\u2013": "-", "\u2026": "...",
        "\u201c": '"',  "\u201d": '"',
        "\u2018": "'",  "\u2019": "'",
        "\u2022": "*",  "\u00b7": "*", "\u00a0": " ",
    }
    return "".join(repl.get(c, c if ord(c) < 128 else "?") for c in s)

def split_caret(text):
    a = text.find("^")
    if a == -1:
        return text.replace("^", ""), None, ""
    b = text.find("^", a + 1)
    if b == -1:
        return text.replace("^", ""), None, ""
    return text[:a].replace("^", ""), text[a+1:b], text[b+1:].replace("^", "")

def layout_quote(qgroup, quote_text, y_start, y_max):
    while len(qgroup):
        qgroup.pop()

    pre, mid, post = split_caret(quote_text)
    tokens = []
    if pre:
        tokens += [(w, False) for w in pre.split()]
    if mid is not None:
        tokens += [(mid, True)]
    if post:
        tokens += [(w, False) for w in post.split()]

    def measure(txt):
        lb = label.Label(terminalio.FONT, text=txt)
        bb = lb.bounding_box
        return bb[2], bb[3]

    space_w, line_h = measure(" ")
    x, y = MARGIN, y_start
    line, line_w = [], 0

    def flush(ln):
        nonlocal x, y
        if y + line_h > y_max:
            return False
        x = MARGIN
        for txt, bold in ln:
            lb = label.Label(terminalio.FONT, text=txt, color=0x000000,
                             anchor_point=(0, 0), anchored_position=(x, y))
            qgroup.append(lb)
            if bold:
                lb2 = label.Label(terminalio.FONT, text=txt, color=0x000000,
                                  anchor_point=(0, 0),
                                  anchored_position=(x + 1, y))
                qgroup.append(lb2)
            x += lb.bounding_box[2] + space_w
        return True

    for txt, bold in tokens:
        w, _ = measure(txt)
        projected = MARGIN + (line_w + space_w + w if line else w) + MARGIN
        if line and projected > display.width:
            if not flush(line):
                return
            y += line_h + 2
            line, line_w = [(txt, bold)], w
        else:
            line.append((txt, bold))
            line_w = (line_w + space_w + w) if line_w else w

    if line:
        flush(line)

def fmt_time(h, m):
    if TIME_24H:
        return f"{h:02d}:{m:02d}"
    suf = "AM" if h < 12 else "PM"
    hh = h % 12 or 12
    return f"{hh}:{m:02d} {suf}"

def pick_quote(h, m):
    key = f"{h:02d}:{m:02d}"
    lst = QUOTES.get(key)
    if not lst:
        bm = (m // UPDATE_MINUTES) * UPDATE_MINUTES
        lst = QUOTES.get(f"{h:02d}:{bm:02d}")
    if not lst:
        lst = QUOTES[next(iter(QUOTES))]
    return lst[h % len(lst)]

# ------------------------------------------------------------------ #
#  Render one frame
# ------------------------------------------------------------------ #
def render(h, m, status_text=""):
    quote_text, work, author, _ = pick_quote(h, m)
    quote_text = sanitize_ascii(quote_text)

    meta_label.text = sanitize_ascii(f"{work} - {author}")
    meta_y = display.height - MARGIN
    meta_label.anchored_position = (MARGIN, meta_y)
    meta_h = meta_label.bounding_box[3]

    time_label.text = sanitize_ascii(
        status_text if status_text else ("Time: " + fmt_time(h, m))
    )
    time_h = time_label.bounding_box[3]
    time_y = meta_y - meta_h - 4
    time_label.anchored_position = (MARGIN, time_y)

    layout_quote(quote_group, quote_text, MARGIN, time_y - 8)
    safe_refresh()

# ------------------------------------------------------------------ #
#  Deep sleep — wakes on next minute boundary OR any button press
# ------------------------------------------------------------------ #
BUTTON_PINS = (board.BUTTON_A, board.BUTTON_B, board.BUTTON_C, board.BUTTON_D)

def go_to_sleep():
    now = get_now()
    secs_left = max(2, SLEEP_SECONDS - now.tm_sec)

    # Buttons are active-low (pressed = False) with internal pull-up
    pin_alarms = [
        alarm.pin.PinAlarm(pin=p, value=False, pull=True)
        for p in BUTTON_PINS
    ]
    time_alarm = alarm.time.TimeAlarm(
        monotonic_time=time.monotonic() + secs_left
    )
    # Never returns — MCU resets and code.py restarts from top on wake
    alarm.exit_and_deep_sleep_until_alarms(time_alarm, *pin_alarms)

# ------------------------------------------------------------------ #
#  Button polling (direct digitalio — no keypad module needed)
# ------------------------------------------------------------------ #
def read_buttons():
    """Return set of currently pressed button indices (active-low)."""
    pressed = set()
    for i, pin in enumerate(BUTTON_PINS):
        with digitalio.DigitalInOut(pin) as btn:
            btn.switch_to_input(pull=digitalio.Pull.UP)
            if not btn.value:
                pressed.add(i)
    return pressed

def wait_next_press(timeout=30):
    """
    Block until a new distinct button press, ignoring any already held.
    Returns pressed index or None on timeout.
    """
    initially_held = read_buttons()
    deadline = time.monotonic() + timeout
    prev = set()
    while time.monotonic() < deadline:
        cur = read_buttons() - initially_held
        new = cur - prev
        if new:
            return min(new)
        prev = cur
        time.sleep(0.05)
    return None

# ------------------------------------------------------------------ #
#  Set-time UI — only entered when a button press woke the device
# ------------------------------------------------------------------ #
def run_set_time_ui():
    """
    Simple two-field (hour / minute) editor.
      A = toggle active field (hour ↔ minute)
      B = increment field
      C = decrement field
      D = save and exit
    Abandons after 30s of inactivity to prevent draining battery.
    """
    now    = get_now()
    edit_h = now.tm_hour
    edit_m = now.tm_min
    field  = 0  # 0 = hour, 1 = minute

    def show():
        fname = "HOUR" if field == 0 else "MIN"
        render(edit_h, edit_m,
               status_text=f"SET {fname}  A:fld B:+ C:- D:save")

    show()

    while True:
        idx = wait_next_press(timeout=30)
        if idx is None:
            # Timed out without input — bail out without saving
            break
        if idx == 0:        # A: toggle field
            field = 1 - field
            show()
        elif idx == 1:      # B: +
            if field == 0:
                edit_h = (edit_h + 1) % 24
            else:
                edit_m = (edit_m + 1) % 60
            show()
        elif idx == 2:      # C: -
            if field == 0:
                edit_h = (edit_h - 1) % 24
            else:
                edit_m = (edit_m - 1) % 60
            show()
        elif idx == 3:      # D: save
            set_now(edit_h, edit_m, 0)
            break

    # Final render with committed time before going back to sleep
    now = get_now()
    render(now.tm_hour, now.tm_min)

# ------------------------------------------------------------------ #
#  MAIN — executes fresh on every wake cycle
# ------------------------------------------------------------------ #
wake = alarm.wake_alarm  # None on cold boot/USB reset

if isinstance(wake, alarm.pin.PinAlarm):
    # A button woke us — short debounce then enter set-time UI
    time.sleep(0.05)
    run_set_time_ui()
else:
    # TimeAlarm or cold boot — standard clock render
    now = get_now()
    render(now.tm_hour, now.tm_min)

# Either path ends here — sleep until next minute or button press
go_to_sleep()