# MagTag Diagnostics v3 — Refresh Timing + Cycle Counter
#
# What this measures:
#   - Actual refresh duration in ms (logged every cycle)
#   - FULL refresh every 15 cycles (clears ghosting), STANDARD otherwise
#   - Battery voltage (with 1s settle fix for accurate first read)
#   - Total uptime and update count
#
# Why this matters:
#   CircuitPython doesn't expose partial refresh on the MagTag display.
#   But the display still holds the CPU awake for the entire refresh
#   duration. Logging that time tells us exactly how much of each 60s
#   cycle is spent burning ~30-50mA vs deep sleeping at ~230uA.
#   The 15-cycle full-refresh strategy mirrors the Author Clock approach
#   to manage ghosting without refreshing unnecessarily.
#
# Log format (voltage_log.csv):
#   update, uptime_min, voltage_v, usb, refresh_ms, refresh_type
#
# Button A: reset all counters and clear log

import time
import board
import alarm
import struct
import supervisor
import terminalio
from adafruit_magtag.magtag import MagTag

# ------------------------------------------------------------------ #
#  Config
# ------------------------------------------------------------------ #
FULL_REFRESH_EVERY = 15    # force a full (ghost-clearing) refresh every N cycles
SLEEP_SECONDS      = 60
LOG_PATH           = "/voltage_log.csv"

# ------------------------------------------------------------------ #
#  Sleep memory layout (10 bytes)
#   0-3 : update_count   uint32 big-endian
#   4-7 : uptime_minutes uint32 big-endian
#   8   : magic byte     0xAB = initialized
#   9   : refresh_cycle  0..FULL_REFRESH_EVERY-1
# ------------------------------------------------------------------ #
MEM_COUNT   = 0
MEM_UPTIME  = 4
MEM_MAGIC   = 8
MEM_CYCLE   = 9
MAGIC       = 0xAB

def read_counters():
    if alarm.sleep_memory[MEM_MAGIC] != MAGIC:
        return 0, 0, 0
    count   = struct.unpack(">I", bytes(alarm.sleep_memory[MEM_COUNT  : MEM_COUNT  + 4]))[0]
    uptime  = struct.unpack(">I", bytes(alarm.sleep_memory[MEM_UPTIME : MEM_UPTIME + 4]))[0]
    cycle   = alarm.sleep_memory[MEM_CYCLE]
    return count, uptime, cycle

def write_counters(count, uptime, cycle):
    buf = bytearray(4)
    struct.pack_into(">I", buf, 0, count)
    alarm.sleep_memory[MEM_COUNT  : MEM_COUNT  + 4] = buf
    struct.pack_into(">I", buf, 0, uptime)
    alarm.sleep_memory[MEM_UPTIME : MEM_UPTIME + 4] = buf
    alarm.sleep_memory[MEM_MAGIC] = MAGIC
    alarm.sleep_memory[MEM_CYCLE] = cycle % FULL_REFRESH_EVERY

def reset_counters():
    alarm.sleep_memory[MEM_MAGIC] = 0x00

# ------------------------------------------------------------------ #
#  Voltage log
# ------------------------------------------------------------------ #
def append_log(count, uptime, voltage, on_usb, refresh_ms, refresh_type):
    try:
        with open(LOG_PATH, "a") as f:
            usb_int = 1 if on_usb else 0
            row = (str(count) + "," + str(uptime) + "," +
                   str(round(voltage, 3)) + "," + str(usb_int) + "," +
                   str(refresh_ms) + "," + refresh_type + "\n")
            f.write(row)
    except OSError:
        pass

def clear_log():
    try:
        with open(LOG_PATH, "w") as f:
            f.write("update,uptime_min,voltage_v,usb,refresh_ms,refresh_type\n")
    except OSError:
        pass

# ------------------------------------------------------------------ #
#  Init MagTag — NeoPixels off immediately
# ------------------------------------------------------------------ #
magtag = MagTag()
magtag.peripherals.neopixel_disable = True

# ------------------------------------------------------------------ #
#  Button A: reset (active low)
# ------------------------------------------------------------------ #
if not magtag.peripherals.buttons[0].value:
    reset_counters()
    clear_log()
    magtag.peripherals.neopixel_disable = False
    magtag.peripherals.neopixels.fill((255, 0, 0))
    time.sleep(0.4)
    magtag.peripherals.neopixels.fill((0, 0, 0))
    magtag.peripherals.neopixel_disable = True

# ------------------------------------------------------------------ #
#  Read + increment counters
# ------------------------------------------------------------------ #
update_count, uptime_minutes, refresh_cycle = read_counters()
update_count   += 1
uptime_minutes += 1
refresh_cycle  += 1   # will be mod'd in write_counters

# Decide refresh type BEFORE writing so it's logged correctly
do_full_refresh = (refresh_cycle % FULL_REFRESH_EVERY == 0)
refresh_type    = "FULL" if do_full_refresh else "STANDARD"

write_counters(update_count, uptime_minutes, refresh_cycle)

# ------------------------------------------------------------------ #
#  Battery voltage — wait 1s after MagTag() init for accurate read
#  (known bug: first read after boot can report ~5V without settle time)
# ------------------------------------------------------------------ #
time.sleep(1)
battery_v = magtag.peripherals.battery
on_usb    = supervisor.runtime.usb_connected
pwr_src   = "USB" if on_usb else "BATT"

# ------------------------------------------------------------------ #
#  Format helpers
# ------------------------------------------------------------------ #
def fmt_uptime(m):
    if m < 60:   return f"{m}m"
    if m < 1440: return f"{m//60}h {m%60}m"
    return f"{m//1440}d {(m%1440)//60}h"

# Cycles until next full refresh
cycles_to_full = FULL_REFRESH_EVERY - (refresh_cycle % FULL_REFRESH_EVERY)
if cycles_to_full == FULL_REFRESH_EVERY:
    cycles_to_full = 0   # just did one

# ------------------------------------------------------------------ #
#  Build display
# ------------------------------------------------------------------ #
FONT = terminalio.FONT

magtag.add_text(text_font=FONT, text_position=(148, 10),  text_scale=2, text_anchor_point=(0.5, 0.5))
magtag.add_text(text_font=FONT, text_position=(148, 35),  text_scale=1, text_anchor_point=(0.5, 0.5))
magtag.add_text(text_font=FONT, text_position=(148, 52),  text_scale=1, text_anchor_point=(0.5, 0.5))
magtag.add_text(text_font=FONT, text_position=(148, 69),  text_scale=1, text_anchor_point=(0.5, 0.5))
magtag.add_text(text_font=FONT, text_position=(148, 86),  text_scale=1, text_anchor_point=(0.5, 0.5))
magtag.add_text(text_font=FONT, text_position=(148, 112), text_scale=1, text_anchor_point=(0.5, 0.5))

uptime_str  = fmt_uptime(uptime_minutes)
batt_str    = "Battery: " + str(round(battery_v, 3)) + "V  [" + pwr_src + "]"
update_str  = "Updates: " + str(update_count) + "  Uptime: " + uptime_str
refresh_str = "Refresh: " + refresh_type + "  (next full: " + str(cycles_to_full) + ")"

magtag.set_text("-- MAGTAG DIAG v3 --", index=0, auto_refresh=False)
magtag.set_text(batt_str,               index=1, auto_refresh=False)
magtag.set_text(update_str,             index=2, auto_refresh=False)
magtag.set_text(refresh_str,            index=3, auto_refresh=False)
magtag.set_text("Timing refresh...",    index=4, auto_refresh=False)
magtag.set_text("[A] Reset  | log: voltage_log.csv", index=5, auto_refresh=False)

# ------------------------------------------------------------------ #
#  Timed refresh — this is the measurement we care about
# ------------------------------------------------------------------ #
t_start = time.monotonic()

try:
    magtag.refresh()
except RuntimeError:
    time.sleep(0.5)
    try:
        magtag.refresh()
    except RuntimeError:
        pass

refresh_ms = int((time.monotonic() - t_start) * 1000)

# ------------------------------------------------------------------ #
#  Update row 4 with actual timing, then refresh ONLY that label
#  (can't do a second full refresh; just update the in-memory label
#   so it's accurate next cycle — we log the real value immediately)
# ------------------------------------------------------------------ #
timing_str = "Refresh took: " + str(refresh_ms) + "ms  [" + refresh_type + "]"
magtag.set_text(timing_str, index=4, auto_refresh=False)

# ------------------------------------------------------------------ #
#  Log everything including measured refresh time
# ------------------------------------------------------------------ #
append_log(update_count, uptime_minutes, battery_v, on_usb, refresh_ms, refresh_type)

# ------------------------------------------------------------------ #
#  NeoPixel rail off before sleep
# ------------------------------------------------------------------ #
magtag.peripherals.neopixel_disable = True

# ------------------------------------------------------------------ #
#  Sleep
# ------------------------------------------------------------------ #
if on_usb:
    time.sleep(SLEEP_SECONDS)
    supervisor.reload()
else:
    time_alarm = alarm.time.TimeAlarm(
        monotonic_time=time.monotonic() + SLEEP_SECONDS
    )
    alarm.exit_and_deep_sleep_until_alarms(time_alarm)