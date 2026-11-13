"""Show dictation history, newest first, grouped by day.

Usage:
  python history.py            # last 20 entries
  python history.py 50         # last 50 entries
  python history.py -s redis   # search text (case-insensitive)
"""
import json
import sys
import textwrap
from datetime import date, datetime, timedelta
from pathlib import Path

HISTORY_FILE = Path(__file__).parent / "history.jsonl"


def day_label(d):
    if d == date.today():
        return "TODAY"
    if d == date.today() - timedelta(days=1):
        return "YESTERDAY"
    return d.strftime("%a, %d %b %Y").upper()


def main():
    args = sys.argv[1:]
    search = None
    limit = 20
    if args[:1] == ["-s"] and len(args) > 1:
        search = " ".join(args[1:]).lower()
        limit = None
    elif args:
        limit = int(args[0])

    if not HISTORY_FILE.exists():
        print("No history yet.")
        return
    entries = [json.loads(line) for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if search:
        entries = [e for e in entries if search in e["text"].lower()]
    entries = entries[::-1][:limit]
    if not entries:
        print("No matching entries.")
        return

    current_day = None
    for e in entries:
        t = datetime.fromisoformat(e["time"])
        if t.date() != current_day:
            current_day = t.date()
            print(f"\n{day_label(current_day)}\n" + "-" * 70)
        when = t.strftime("%-I:%M%p").lower()
        body = textwrap.fill(e["text"], width=70, initial_indent=f"{when:>8}   ", subsequent_indent=" " * 11)
        print(body + "\n")


if __name__ == "__main__":
    main()
