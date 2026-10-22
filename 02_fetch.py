#!/usr/bin/env python3
"""Pull N full UTC days for one station from the ALDOT SBC/Xpos archive.

    ./02_fetch.py 2026-09-30 2026-10-01
    ./02_fetch.py --rate 30 --split-hours 6 2026-09-30

Lands in ./data/day_YYYYMMDD/<STATION>/.

By default each day is ONE server request. A whole 1 Hz day (86400 epochs,
~660 MB) was tested against hourly sub-windows on 2026-10-09: identical
observation epochs, ~7.5 min server time. --split-hours N splits the day into
N-hour requests instead; concat_rinex stitches them and drops the epoch
duplicated at each boundary.

Not incremental: re-running overwrites the day directory.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from xpos import XposClient, pull_day      # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("days", nargs="+", help="UTC days YYYY-MM-DD")
    ap.add_argument("--station", default="AL50")
    ap.add_argument("--rate", type=int, default=1, help="seconds (default 1)")
    ap.add_argument("--split-hours", type=int, default=0,
                    help="sub-window size in hours (default 0 = whole day)")
    ap.add_argument("--wait", type=int, default=1800,
                    help="per-request server budget, seconds (default 1800)")
    ap.add_argument("--outdir", type=Path, default=HERE / "data")
    ap.add_argument("--yes", action="store_true", help="skip the prompt")
    args = ap.parse_args()

    try:
        days = [datetime.strptime(d, "%Y-%m-%d").date() for d in args.days]
    except ValueError as e:
        sys.exit(f"STOP: {e}")

    nwin = (24 // args.split_hours) if args.split_hours > 0 else 1
    est_mb = len(days) * 660 * (1 if args.rate == 1 else 1 / args.rate)
    print(f"station      : {args.station}")
    print(f"days         : {', '.join(str(d) for d in days)} "
          f"({len(days)} x 24 h = {len(days) * 24} h)")
    print(f"rate         : {args.rate}s -> {86400 // args.rate} epochs/day expected")
    print(f"split-hours  : {args.split_hours} -> {len(days) * nwin} server request(s)")
    print(f"estimate     : ~{est_mb:,.0f} MB")
    print(f"destination  : {args.outdir}/day_*/{args.station}/")

    if not args.yes:
        if input("\nproceed? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")

    client = XposClient()
    sid = client.site_id(args.station)
    print(f"\nsiteId {sid}")

    problems = []
    for day in days:
        print(f"\n== {day} ==", flush=True)
        try:
            got, exp = pull_day(client, args.station, sid, day,
                                args.rate * 1000, args.split_hours,
                                args.outdir, wait_s=args.wait)
        except SystemExit as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            problems.append((day, str(e)[:160]))
            continue
        pct = 100 * got / exp if exp else 0
        status = "complete" if pct >= 99.9 else "PARTIAL"
        print(f"  {status}: {got}/{exp} epochs ({pct:.2f}%)")
        if pct < 99.9:
            problems.append((day, f"{got}/{exp} epochs"))

    print("\n==== SUMMARY ====")
    print(f"{len(days) - len(problems)}/{len(days)} day(s) complete")
    for day, why in problems:
        print(f"  PROBLEM {day}: {why}")
    print(f"\nverify with:  ./03_verify.py --station {args.station} "
          f"--rate {args.rate} --outdir {args.outdir} "
          + " ".join(str(d) for d in days))


if __name__ == "__main__":
    main()
