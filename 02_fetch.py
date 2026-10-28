#!/usr/bin/env python3
"""Pull full UTC days, or one arbitrary UTC window, for one station from the
ALDOT SBC/Xpos archive.

    ./02_fetch.py 2026-09-30 2026-10-01
    ./02_fetch.py --rate 30 --split-hours 6 2026-09-30
    ./02_fetch.py --start 2026-10-01T21:43 --end 2026-10-01T21:55

Days land in ./data/day_YYYYMMDD/<STATION>/; a --start/--end window lands in
./data/window_YYYYMMDDTHHMMSS-YYYYMMDDTHHMMSS/<STATION>/. Times are UTC
(YYYY-MM-DDTHH:MM[:SS], trailing Z optional). Give days or a window, not both.

By default each day (or window) is ONE server request. A whole 1 Hz day
(86400 epochs, ~660 MB) was tested against hourly sub-windows on 2026-10-09:
identical observation epochs, ~7.5 min server time. --split-hours N splits it
into N-hour requests instead; concat_rinex stitches them and drops the epoch
duplicated at each boundary.

Not incremental: re-running overwrites the output directory.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from xpos import XposClient, parse_utc, pull_window, split_windows   # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("days", nargs="*", help="UTC days YYYY-MM-DD")
    ap.add_argument("--start", type=parse_utc, help="window start, UTC")
    ap.add_argument("--end", type=parse_utc, help="window end, UTC")
    ap.add_argument("--station", default="AL50")
    ap.add_argument("--rate", type=int, default=1, help="seconds (default 1)")
    ap.add_argument("--split-hours", type=int, default=0,
                    help="sub-window size in hours (default 0 = one request)")
    ap.add_argument("--wait", type=int, default=1800,
                    help="per-request server budget, seconds (default 1800)")
    ap.add_argument("--outdir", type=Path, default=HERE / "data")
    ap.add_argument("--yes", action="store_true", help="skip the prompt")
    args = ap.parse_args()

    # jobs: (label, start, end, site_dir, request tag)
    jobs = []
    if args.start or args.end:
        if not (args.start and args.end):
            ap.error("--start and --end go together")
        if args.days:
            ap.error("give days or --start/--end, not both")
        if args.end <= args.start:
            ap.error("--end must be after --start")
        span = f"{args.start:%Y%m%dT%H%M%S}-{args.end:%Y%m%dT%H%M%S}"
        # the API caps request names at 30 chars, so tag by start time only
        jobs.append((f"{args.start:%Y-%m-%d %H:%M:%S} .. "
                     f"{args.end:%Y-%m-%d %H:%M:%S} UTC",
                     args.start, args.end,
                     args.outdir / f"window_{span}" / args.station,
                     f"{args.start:%y%m%dT%H%M}"))
    else:
        if not args.days:
            ap.error("give UTC days YYYY-MM-DD, or --start and --end")
        for d in args.days:
            try:
                day = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError as e:
                sys.exit(f"STOP: {e}")
            jobs.append((f"{day:%Y-%m-%d}", day, day + timedelta(days=1),
                         args.outdir / f"day_{day:%Y%m%d}" / args.station,
                         f"{day:%Y%m%d}"))

    hours = sum((e - s).total_seconds() for _, s, e, _, _ in jobs) / 3600
    nreq = sum(len(split_windows(s, e, args.split_hours))
               for _, s, e, _, _ in jobs)
    print(f"station      : {args.station}")
    print(f"span         : {', '.join(j[0] for j in jobs)} ({hours:g} h)")
    print(f"rate         : {args.rate}s -> {round(hours * 3600 / args.rate)} "
          f"epochs expected")
    print(f"split-hours  : {args.split_hours} -> {nreq} server request(s)")
    print(f"estimate     : ~{hours / 24 * 660 / args.rate:,.0f} MB")
    print(f"destination  : {', '.join(str(j[3]) for j in jobs)}")

    if not args.yes:
        if input("\nproceed? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")

    client = XposClient()
    sid = client.site_id(args.station)
    print(f"\nsiteId {sid}")

    problems = []
    for label, start, end, site_dir, tag in jobs:
        print(f"\n== {label} ==", flush=True)
        try:
            got, exp = pull_window(client, args.station, sid, start, end,
                                   args.rate * 1000, args.split_hours,
                                   site_dir, tag, wait_s=args.wait)
        except SystemExit as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            problems.append((label, str(e)[:160]))
            continue
        pct = 100 * got / exp if exp else 0
        status = "complete" if pct >= 99.9 else "PARTIAL"
        print(f"  {status}: {got}/{exp} epochs ({pct:.2f}%)")
        if pct < 99.9:
            problems.append((label, f"{got}/{exp} epochs"))

    print("\n==== SUMMARY ====")
    print(f"{len(jobs) - len(problems)}/{len(jobs)} complete")
    for label, why in problems:
        print(f"  PROBLEM {label}: {why}")
    if args.days:
        print(f"\nverify with:  ./03_verify.py --station {args.station} "
              f"--rate {args.rate} --outdir {args.outdir} "
              + " ".join(args.days))
    else:
        print(f"\nverify with:  ./03_verify.py --rate {args.rate} "
              f"{jobs[0][3]}/*.*o")


if __name__ == "__main__":
    main()
