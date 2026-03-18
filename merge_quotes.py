#!/usr/bin/env python3
"""
merge_quotes.py — Combine two literary clock CSV files.

Usage:
    python3 merge_quotes.py                        # uses defaults below
    python3 merge_quotes.py a.csv b.csv out.csv    # explicit paths

Priority: FILE_A entries are kept over FILE_B when the quote text is a
near-duplicate (same time, very similar text). Original quotes with
hand-crafted ^bold spans^ are preserved.

Output columns: hhmm|quote|work|author|tag
"""

import re
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
FILE_A  = "quotes.csv"           # your existing file (higher priority)
FILE_B  = "quotes_full.csv"      # converted JohannesNE file
OUTPUT  = "quotes_merged.csv"

if len(sys.argv) == 4:
    FILE_A, FILE_B, OUTPUT = sys.argv[1], sys.argv[2], sys.argv[3]

# ── Helpers ────────────────────────────────────────────────────────────────────

def normalise_hhmm(raw: str) -> str | None:
    """Return 'HHMM' string or None if unparseable."""
    digits = raw.strip().strip('"').replace(":", "")
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) == 4 and digits.isdigit():
        return digits
    return None


def strip_carets(text: str) -> str:
    return text.replace("^", "")


def fingerprint(quote: str) -> str:
    """Normalised lowercase, no punctuation — for fuzzy dedup."""
    s = strip_carets(quote).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_file(path: str) -> list[dict]:
    """
    Parse a pipe-delimited CSV into a list of row dicts.
    Handles both 4-column and 5-column (with tag) formats,
    and the 6-column JohannesNE format (with time_phrase as col 2).
    """
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip().strip('"')
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) < 4:
                    continue

                # Detect JohannesNE 6-col format: HH:MM|phrase|quote|work|author|tag
                # vs our 5-col format:             HHMM|quote|work|author|tag
                # Heuristic: if col[0] contains ':' and col[2] is long, it's 6-col
                if ":" in parts[0] and len(parts) >= 5 and len(parts[2]) > len(parts[1]):
                    hhmm   = normalise_hhmm(parts[0])
                    phrase = parts[1].strip()
                    quote  = parts[2].strip().strip('"')
                    work   = parts[3].strip()
                    author = parts[4].strip()
                    tag    = parts[5].strip() if len(parts) > 5 else "unknown"

                    # Insert ^carets^ around the time phrase
                    pattern = re.escape(phrase)
                    quote, n = re.subn(
                        f"({pattern})", r"^\1^", quote, count=1, flags=re.IGNORECASE
                    )
                    if n == 0:
                        pass  # phrase not found verbatim — leave without bold

                else:
                    hhmm   = normalise_hhmm(parts[0])
                    quote  = parts[1].strip().strip('"')
                    work   = parts[2].strip() if len(parts) > 2 else ""
                    author = parts[3].strip() if len(parts) > 3 else ""
                    tag    = parts[4].strip() if len(parts) > 4 else "unknown"

                if hhmm is None:
                    continue

                # Strip stray HTML
                quote = re.sub(r"<br\s*/?>", " ", quote)
                quote = re.sub(r"<[^>]+>", "", quote)

                rows.append({
                    "hhmm":   hhmm,
                    "quote":  quote,
                    "work":   work,
                    "author": author,
                    "tag":    tag,
                })
    except FileNotFoundError:
        print(f"  ⚠  File not found: {path}")
    return rows


# ── Load both files ────────────────────────────────────────────────────────────
print(f"Loading {FILE_A} …")
rows_a = parse_file(FILE_A)
print(f"  {len(rows_a)} quotes")

print(f"Loading {FILE_B} …")
rows_b = parse_file(FILE_B)
print(f"  {len(rows_b)} quotes")

# ── Merge ──────────────────────────────────────────────────────────────────────
# Build a dict: hhmm → [list of rows], starting with FILE_A (priority).
# For each FILE_B row, skip it if a near-duplicate already exists for that minute.

merged: dict[str, list[dict]] = {}

for row in rows_a:
    merged.setdefault(row["hhmm"], []).append(row)

dupes  = 0
added  = 0

for row in rows_b:
    key        = row["hhmm"]
    fp_new     = fingerprint(row["quote"])
    existing   = merged.get(key, [])
    fp_existing = {fingerprint(r["quote"]) for r in existing}

    # Check for near-duplicate: if the first 60 chars of the fingerprint match
    is_dupe = any(
        fp_new[:60] == fp_ex[:60]
        for fp_ex in fp_existing
    )

    if is_dupe:
        dupes += 1
    else:
        merged.setdefault(key, []).append(row)
        added += 1

# ── Write output ───────────────────────────────────────────────────────────────
all_rows = []
for hhmm in sorted(merged.keys()):
    all_rows.extend(merged[hhmm])

with open(OUTPUT, "w", encoding="utf-8") as f:
    for row in all_rows:
        f.write(f"{row['hhmm']}|{row['quote']}|{row['work']}|{row['author']}|{row['tag']}\n")

# ── Summary ────────────────────────────────────────────────────────────────────
total_minutes  = len(merged)
covered        = sum(1 for v in merged.values() if v)
multi          = sum(1 for v in merged.values() if len(v) > 1)
missing        = [f"{int(k[:2]):02d}:{int(k[2:]):02d}"
                  for k in (f"{h:02d}{m:02d}" for h in range(24) for m in range(60))
                  if k not in merged]

print(f"\nMerge complete → {OUTPUT}")
print(f"  From {FILE_A}:   {len(rows_a)} quotes")
print(f"  From {FILE_B}:   {len(rows_b)} quotes")
print(f"  Duplicates skipped: {dupes}")
print(f"  New quotes added:   {added}")
print(f"  Total quotes:       {len(all_rows)}")
print(f"  Minutes covered:    {covered} / 1440")
print(f"  Minutes with multiple quotes: {multi}")

if missing:
    print(f"\n  Still missing {len(missing)} minutes, e.g.:", ", ".join(missing[:12]),
          "…" if len(missing) > 12 else "")
else:
    print("\n  All 1440 minutes covered!")

print(f"\nReview {OUTPUT}, then replace quotes.csv when happy:")
print(f"  cp {OUTPUT} quotes.csv")