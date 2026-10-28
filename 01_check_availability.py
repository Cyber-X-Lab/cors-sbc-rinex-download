#!/usr/bin/env python3
"""Read-only availability probe against the ALDOT SBC/Xpos archive.

Asks "does this station have data at this rate for this span, and how much?"
without queueing a request or downloading anything -- it calls only the
`rinex/zulu/preview` endpoint.

Usage
-----
    ./01_check_availability.py --last 4
    ./01_check_availability.py --day 2026-09-30 --day 2026-10-01
    ./01_check_availability.py --start 2025-11-26T21:43 --end 2025-11-26T21:55 --rate 1

Prints one row per (span, rate):

    start  end  rate_s  epochs  expected  completeness  verdict

verdict is AVAILABLE (complete), PARTIAL (some epochs) or NONE. It is judged
from the epoch count and dataCompleteness, not fileExists: the server lists a
file for spans it holds nothing for (fileExists true, 0 epochs).
The server counts whole archive files (15 min at 1 Hz), so for a short or
unaligned window `epochs` can exceed `expected`; the download is still cut to
the window.
Times are UTC (YYYY-MM-DDTHH:MM[:SS], trailing Z optional); end is exclusive.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xpos import XposClient, format_utc, parse_utc     # noqa: E402


def verdict(epochs, completeness):
    if not epochs or not completeness:
        return "NONE"
    return "AVAILABLE" if completeness >= 0.999 else "PARTIAL"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--station", default="AL50")
    ap.add_argument("--day", action="append", default=[],
                    help="UTC day YYYY-MM-DD (repeatable)")
    ap.add_argument("--last", type=int, default=0,
                    help="probe the last N complete UTC days")
    ap.add_argument("--start", type=parse_utc, help="window start, UTC")
    ap.add_argument("--end", type=parse_utc, help="window end, UTC")
    ap.add_argument("--rate", action="append", type=int, default=[],
                    help="rate in seconds (repeatable; default 1 and 30)")
    args = ap.parse_args()

    days = [datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            for d in args.day]
    if args.last:
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0)
        days += [today - timedelta(days=i) for i in range(1, args.last + 1)]
    spans = [(d, d + timedelta(days=1)) for d in sorted(set(days))]
    if args.start or args.end:
        if not (args.start and args.end):
            ap.error("--start and --end go together")
        if args.end <= args.start:
            ap.error("--end must be after --start")
        spans.append((args.start, args.end))
    if not spans:
        ap.error("give --day, --last and/or --start/--end")
    rates = sorted(set(args.rate or [1, 30]))

    client = XposClient()
    sid = client.site_id(args.station)
    print(f"# station {args.station} (siteId {sid}); preview only, nothing "
          f"queued or downloaded", file=sys.stderr)

    print(f"{'start':<21}{'end':<21}{'rate_s':>7}{'epochs':>8}{'expected':>9}"
          f"{'completeness':>13}  verdict")
    for start, end in spans:
        s, e = format_utc(start), format_utc(end)
        for r in rates:
            expected = round((end - start).total_seconds() / r)
            try:
                pv = client.preview(sid, s, e, r * 1000)
            except SystemExit as ex:       # client exits on API error
                print(f"{s:<21}{e:<21}{r:>7}{'-':>8}{expected:>9}{'-':>13}  "
                      f"ERROR {ex}")
                continue
            epochs = pv.get("epochs") or 0
            comp = min((f.get("dataCompleteness") or 0
                        for f in pv.get("files", [])), default=0)
            print(f"{s:<21}{e:<21}{r:>7}{epochs:>8}{expected:>9}{comp:>13.3f}  "
                  f"{verdict(epochs, comp)}")


if __name__ == "__main__":
    main()
