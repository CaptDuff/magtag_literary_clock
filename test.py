
import datetime, config
config.load()
now = datetime.datetime.now()
print('Time:', now.strftime('%H:%M'))
h, m = now.hour, now.minute
print('h=%d m=%d' % (h, m))

# Simulate what pick_quote does
import csv
with open('quotes.csv') as f:
    rows = list(csv.reader(f, delimiter='|'))
print('Total rows:', len(rows))
target = h * 60 + m
print('Target minutes:', target)

# Find nearest
best = None
for row in rows:
    if not row or len(row) < 2: continue
    try:
        t = int(row[0])
        tm = (t // 100) * 60 + (t % 100)
        if best is None or abs(tm - target) < abs((best[0]//100)*60+(best[0]%100) - target):
            best = row
    except: pass
print('Best match:', best[0] if best else 'None', best[1][:60] if best else '')
