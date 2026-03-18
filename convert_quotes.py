#!/usr/bin/env python3
"""
Convert JohannesNE litclock_annotated.csv → your quotes.csv format.

Input:  HH:MM | time_phrase | quote | work | author | sfw
Output: HHMM  | quote with ^time_phrase^ bolded | work | author | tag
"""

import re

INPUT  = "litclock_annotated.csv"
OUTPUT = "quotes_full.csv"

written = 0
skipped = 0

with open(INPUT, encoding="utf-8") as fin, \
     open(OUTPUT, "w", encoding="utf-8") as fout:

    for raw in fin:
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 5:
            skipped += 1
            continue

        hhmm        = parts[0].strip()   # HH:MM
        time_phrase = parts[1].strip()   # e.g. "half past five"
        quote       = parts[2].strip()   # full quote text
        work        = parts[3].strip()
        author      = parts[4].strip()
        tag         = parts[5].strip() if len(parts) > 5 else "unknown"

        # Normalise HH:MM → HHMM
        hhmm_out = hhmm.replace(":", "")
        if len(hhmm_out) != 4:
            skipped += 1
            continue

        # Wrap the time phrase in ^carets^ (case-insensitive, first match only)
        # Escape any regex special chars in the time phrase
        pattern = re.escape(time_phrase)
        bolded, n = re.subn(f"({pattern})", r"^\1^", quote, count=1, flags=re.IGNORECASE)

        if n == 0:
            # Phrase not found verbatim — write quote as-is (no bold span)
            bolded = quote

        # Strip any HTML that some entries contain
        bolded = re.sub(r"<br\s*/?>", " ", bolded)
        bolded = re.sub(r"<[^>]+>", "", bolded)

        fout.write(f"{hhmm_out}|{bolded}|{work}|{author}|{tag}\n")
        written += 1

print(f"Done. {written} quotes written → {OUTPUT}")
print(f"      {skipped} rows skipped (malformed).")
print(f"\nReview quotes_full.csv, then replace quotes.csv when happy:")
print(f"  cp quotes_full.csv quotes.csv")