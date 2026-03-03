# MagTag Diagnostics Test
# Displays: battery voltage, update counter, uptime (in minutes)
# Button A (leftmost) = reset all counters
# Updates every 60 seconds, deep sleeps on battery, loops on USB
#
# Uses alarm.sleep_memory to persist counters across deep sleep cycles.
# sleep_memory is cleared on power loss or hard reset — not by deep sleep.
#
# Layout (296x128 display):
#   Line 1: Title
#   Line 2: Battery voltage + USB/BATT indicator
#   Line 3: Update counter
#   Line 4: Uptime in minutes
#   Line 5: Reset hint

import time
import board
import alarm
import supervisor
import struct
import terminalio
import displayio
from adafruit_magtag.magtag import MagTag

# ---------------------------------------------------------------------------
# Sleep memory layout (8 bytes total used)
#   bytes 0-3 : update_count  (uint32, big-endian)
#   bytes 4-7 : uptime_minutes (uint32, big-endian)
# ---------------------------------------------------------------------------
MEM_OFFSET_COUNT   = 0
MEM_OFFSET_UPTIME  = 4
MAGIC_BYTE_OFFSET  = 8   # byte 8 used as init flag
MAGIC_VALUE        = 0xAB

def read_counters():
    if alarm.sleep_memory[MAGIC_BYTE_OFFSET] != MAGIC_VALUE:
        # First boot or hard reset — initialize
        return 0, 0
    count  = struct.unpack(">I", bytes(alarm.sleep_memory[MEM_OFFSET_COUNT  : MEM_OFFSET_COUNT  + 4]))[0]
    uptime = struct.unpack(">I", bytes(alarm.sleep_memory[MEM_OFFSET_UPTIME : MEM_OFFSET_UPTIME + 4]))[0]
    return count, uptime

def write_counters(count, uptime):
    buf = bytearray(4)
    struct.pack_into(">I", buf, 0, count)
    alarm.sleep_memory[MEM_OFFSET_COUNT : MEM_OFFSET_COUNT + 4] = buf
    struct.pack_into(">I", buf, 0, uptime)
    alarm.sleep_memory[MEM_OFFSET_UPTIME : MEM_OFFSET_UPTIME + 4] = buf
    alarm.sleep_memory[MAGIC_BYTE_OFFSET] = MAGIC_VALUE

def reset_counters():
    alarm.sleep_memory[MAGIC_BYTE_OFFSET] = 0x00  # clears magic → fresh start

# ---------------------------------------------------------------------------
# Init MagTag
# ---------------------------------------------------------------------------
magtag = MagTag()
magtag.peripherals.neopixel_disable = True   # save power

# ---------------------------------------------------------------------------
# Check for Button A press (reset)
# Buttons are active LOW — pressed = False
# ---------------------------------------------------------------------------
buttons = magtag.peripherals.buttons
if not buttons[0].value:                     # Button A = index 0 (leftmost)
    reset_counters()
    # Brief NeoPixel flash to confirm reset
    magtag.peripherals.neopixel_disable = False
    magtag.peripherals.neopixels.fill((255, 0, 0))
    time.sleep(0.5)
    magtag.peripherals.neopixels.fill((0, 0, 0))
    magtag.peripherals.neopixel_disable = True

# ---------------------------------------------------------------------------
# Read + increment counters
# ---------------------------------------------------------------------------
update_count, uptime_minutes = read_counters()
update_count   += 1
uptime_minutes += 1
write_counters(update_count, uptime_minutes)

# ---------------------------------------------------------------------------
# Read battery
# ---------------------------------------------------------------------------
battery_v    = magtag.peripherals.battery
on_usb       = supervisor.runtime.usb_connected
power_source = "USB" if on_usb else "BATT"

# ---------------------------------------------------------------------------
# Format uptime nicely
# ---------------------------------------------------------------------------
def fmt_uptime(minutes):
    if minutes < 60:
        return f"{minutes} min"
    elif minutes < 1440:
        h = minutes // 60
        m = minutes % 60
        return f"{h}h {m}m"
    else:
        d = minutes // 1440
        h = (minutes % 1440) // 60
        return f"{d}d {h}h"

# ---------------------------------------------------------------------------
# Build display text blocks
# Using MagTag's add_text() system — 5 rows
# Display is 296x128 px; terminalio.FONT is 6x14px per char at scale 1
# ---------------------------------------------------------------------------
FONT = terminalio.FONT

magtag.add_text(
    text_font=FONT,
    text_position=(148, 10),
    text_scale=2,
    text_anchor_point=(0.5, 0.5),
)

magtag.add_text(
    text_font=FONT,
    text_position=(148, 38),
    text_scale=1,
    text_anchor_point=(0.5, 0.5),
)

magtag.add_text(
    text_font=FONT,
    text_position=(148, 58),
    text_scale=1,
    text_anchor_point=(0.5, 0.5),
)

magtag.add_text(
    text_font=FONT,
    text_position=(148, 78),
    text_scale=1,
    text_anchor_point=(0.5, 0.5),
)

magtag.add_text(
    text_font=FONT,
    text_position=(148, 108),
    text_scale=1,
    text_anchor_point=(0.5, 0.5),
)

# Set text values
magtag.set_text("-- MAGTAG DIAG --", index=0, auto_refresh=False)
magtag.set_text(f"Battery: {battery_v:.2f}V  [{power_source}]", index=1, auto_refresh=False)
magtag.set_text(f"Updates:  {update_count}", index=2, auto_refresh=False)
magtag.set_text(f"Uptime:   {fmt_uptime(uptime_minutes)}", index=3, auto_refresh=False)
magtag.set_text("[A] Reset counters", index=4, auto_refresh=False)

# Trigger single display refresh
magtag.refresh()

# Brief pause to let the e-ink finish drawing before sleep
time.sleep(2)

# ---------------------------------------------------------------------------
# Sleep strategy: deep sleep on battery, regular sleep on USB
# Deep sleep wakes after 60s and re-runs code.py from the top.
# USB mode loops here so serial console stays alive for debugging.
# ---------------------------------------------------------------------------
SLEEP_SECONDS = 60

if on_usb:
    # Stay awake — useful for serial console debugging
    print(f"USB mode: sleeping {SLEEP_SECONDS}s then looping")
    time.sleep(SLEEP_SECONDS)
    # Soft reset to re-run code.py (equivalent to a wake from deep sleep)
    supervisor.reload()
else:
    # True deep sleep — ~250uA draw while asleep
    time_alarm = alarm.time.TimeAlarm(
        monotonic_time=time.monotonic() + SLEEP_SECONDS
    )
    alarm.exit_and_deep_sleep_until_alarms(time_alarm)
    