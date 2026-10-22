#!/usr/bin/env python3
"""Read-only availability probe against the ALDOT SBC/Xpos archive.

You may wonder "does this station actually have 1 Hz data on these days, and is it
complete?" without queueing a request or downloading anything -- it calls only
the `rinex/zulu/preview` endpoint.

Usage
-----
    ./01_check_availability.py --last 4
    ./01_check_availability.py --day 2026-09-30 --day 2026-10-01
    ./01_check_availability.py --day 2026-07-07 --hour 18
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xpos import XposClient, iso          # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--station", default="AL50")
    ap.add_argument("--day", action="append", default=[],
                    help="UTC day YYYY-MM-DD (repeatable)")
    ap.add_argument("--last", type=int, default=0,
                    help="probe the last N complete UTC days")
    ap.add_argument("--rate", action="append", type=int, default=[],
                    help="rate in seconds (repeatable; default 1 and 30)")
    ap.add_argument("--hour", type=int, default=None,
                    help="probe only this UTC hour instead of the full day")
    args = ap.parse_args()

    days = [datetime.strptime(d, "%Y-%m-%d").date() for d in args.day]
    if args.last:
        today = datetime.now(timezone.utc).date()
        days += [today - timedelta(days=i) for i in range(1, args.last + 1)]
    if not days:
        ap.error("give --day YYYY-MM-DD (repeatable) and/or --last N")
    days = sorted(set(days))
    rates = sorted(set(args.rate or [1, 30]))

    client = XposClient()
    sid = client.site_id(args.station)
    scope = f"hour {args.hour:02d}Z" if args.hour is not None else "full UTC day"
    print(f"station {args.station} -> siteId {sid}")
    print(f"probing {len(days)} day(s) x {len(rates)} rate(s), scope: {scope}\n")

    hdr = f"{'day':<12}{'rate':>6}{'files':>7}{'exists':>8}  completeness"
    print(hdr)
    print("-" * (len(hdr) + 10))

    verdict = {}
    for d in days:
        if args.hour is not None:
            s = iso(datetime(d.year, d.month, d.day, args.hour,
                             tzinfo=timezone.utc))
            e = iso(datetime(d.year, d.month, d.day, args.hour,
                             tzinfo=timezone.utc) + timedelta(hours=1))
        else:
            s = iso(datetime(d.year, d.month, d.day, tzinfo=timezone.utc))
            e = iso(datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
                    + timedelta(days=1))
        for r in rates:
            try:
                pv = client.preview(sid, s, e, r * 1000)
                files = pv.get("files", [])
                have = [f for f in files if f.get("fileExists")]
                comp = [f.get("dataCompleteness") for f in have]
                print(f"{d.isoformat():<12}{r:>5}s{len(files):>7}{len(have):>8}  "
                      f"{comp if comp else '-'}")
                verdict[(d, r)] = len(have)
            except SystemExit as ex:      # client exits on API error
                print(f"{d.isoformat():<12}{r:>5}s{'':>7}{'ERR':>8}  {ex}")
                verdict[(d, r)] = 0

    print("\n==== VERDICT ====")
    usable = []
    for d in days:
        one = verdict.get((d, 1), 0)
        slow = max((verdict.get((d, r), 0) for r in rates if r != 1), default=0)
        if one:
            print(f"{d}: 1 Hz AVAILABLE ({one} file(s))")
            usable.append(d)
        elif slow:
            print(f"{d}: 1 Hz PURGED — lower-rate exists, high-rate does not")
        else:
            print(f"{d}: NO DATA at any probed rate")

    if len(usable) >= 2:
        print(f"\nFor 48 h data:  ./02_fetch.sh {usable[-2]} {usable[-1]}")
    print("\nNothing was queued or downloaded; preview-only probe.")


if __name__ == "__main__":
    main()
